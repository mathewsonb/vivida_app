import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { supabase } from './lib/supabaseClient.js';
import Header from './components/Header';
import SwipeDeck from './components/SwipeDeck';
import EmailPromptModal from './components/EmailPromptModal';
import { ThumbsDown, Heart, Flame, Sparkles, RotateCcw, AlertTriangle, X } from 'lucide-react';
import HistoryPage from './components/HistoryPage';
import { getCurrentMood, recordSwipe, setCurrentMood, applyVectorDrift } from './services/historyService';
import { getOrCreateClientId } from './utils/session';

/**
 * Calculates Euclidean similarity score between user mood and event vector in [0, 1]^3 space.
 * Uses a Gaussian similarity kernel: Sim = exp(- distSquared / (2 * sigma^2))
 * 
 * @param {Object} userMood Current user mood vector { energy, social, novelty }
 * @param {Object} event Event object with coordinate vectors
 * @returns {number} Normalized similarity score between 0.0 and 1.0
 */
const calculateEuclideanSimilarity = (userMood, event) => {
  if (!userMood || !event) return 0;

  const uE = Number(userMood.energy ?? 0.5);
  const uS = Number(userMood.social ?? 0.5);
  const uN = Number(userMood.novelty ?? 0.5);

  const eE = Number(event.energy ?? event.energy_vector ?? 0.5);
  const eS = Number(event.social ?? event.social_vector ?? 0.5);
  const eN = Number(event.novelty ?? event.novelty_vector ?? 0.5);

  // Squared 3D Euclidean distance: (ΔE)^2 + (ΔS)^2 + (ΔN)^2
  const distSquared = (uE - eE) ** 2 + (uS - eS) ** 2 + (uN - eN) ** 2;

  // Gaussian decay kernel (sigma = 0.5 provides strict penalties for single-axis variance)
  const SIGMA = 0.5;
  const similarityScore = Math.exp(-distSquared / (2 * (SIGMA ** 2)));

  return Number(similarityScore.toFixed(4));
};

export default function App() {
  const [rawEvents, setRawEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [sessionKey, setSessionKey] = useState('');
  const [userHash, setUserHash] = useState(null);
  const [currentView, setCurrentView] = useState('deck'); // 'deck' | 'history'
  const [isInitialPromptOpen, setIsInitialPromptOpen] = useState(false);
  const [isResetModalOpen, setIsResetModalOpen] = useState(false);

  const [bookmarks, setBookmarks] = useState(() => {
    try {
      const saved = localStorage.getItem('vivida_bookmarks');
      return saved ? JSON.parse(saved) : [];
    } catch {
      return [];
    }
  });

  const [mood, setMood] = useState(() => getCurrentMood());

  // Keep LocalStorage synchronized when user manually moves sliders via Header/MoodSliders
  const handleMoodChange = useCallback((newMood) => {
    const updated = setCurrentMood(newMood);
    setMood(updated);
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem('vivida_bookmarks', JSON.stringify(bookmarks));
    } catch (err) {
      console.warn('Failed to persist bookmarks to localStorage:', err);
    }
  }, [bookmarks]);

  const extractDatesFromSchedule = (schedule) => {
    if (!schedule) return [];
    let parsed = schedule;
    if (typeof parsed === 'string') {
      try {
        parsed = JSON.parse(parsed);
        if (typeof parsed === 'string') parsed = JSON.parse(parsed);
      } catch {
        return [];
      }
    }

    const dates = [];
    if (Array.isArray(parsed)) {
      parsed.forEach((item) => {
        const val = item?.start_time || item?.date || item?.time;
        if (val) dates.push(String(val));
      });
    } else if (typeof parsed === 'object' && parsed !== null) {
      if (parsed.start_time) {
        dates.push(String(parsed.start_time));
      } else {
        Object.keys(parsed).forEach((k) => dates.push(k));
      }
    }
    return dates;
  };

  const fetchEvents = useCallback(async () => {
    setLoading(true);
    try {
      if (!supabase) return;

      // 1. Collect swiped IDs from localStorage first (fast, offline-ready)
      const localSwipedIds = new Set();
      try {
        const localHistory = JSON.parse(localStorage.getItem('vibe_history') || '[]');
        if (Array.isArray(localHistory)) {
          localHistory.forEach((item) => {
            if (item?.event_id) localSwipedIds.add(String(item.event_id));
          });
        }
      } catch (err) {
        console.warn('Failed to read vibe_history from localStorage:', err);
      }

      // 2. Fetch swiped IDs from Supabase for authenticated/tracked users
      const activeUserIdentifier = userHash || sessionKey;
      if (activeUserIdentifier) {
        const { data: interactions } = await supabase
          .from('user_interactions')
          .select('event_id')
          .eq('user_id', activeUserIdentifier);

        if (interactions && interactions.length > 0) {
          interactions.forEach((item) => {
            if (item?.event_id) localSwipedIds.add(String(item.event_id));
          });
        }
      }

      const swipedArray = Array.from(localSwipedIds);

      // 3. Fetch up to 1,000 events directly from Supabase
      let query = supabase
        .from('events')
        .select('*')
        .range(0, 999);

      // 4. Exclude swiped IDs if history exists
      if (swipedArray.length > 0) {
        query = query.not('id', 'in', `(${swipedArray.join(',')})`);
      }

      const { data, error } = await query;
      if (error) throw error;

      // 5. Filter out expired events
      const now = new Date();
      const localTodayStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;

      const validEvents = (data || []).filter((event) => {
        if (localSwipedIds.has(String(event.id))) return false;

        if (!event.schedule) return true;
        const dateList = extractDatesFromSchedule(event.schedule);
        if (dateList.length === 0) return true;

        return dateList.some((d) => {
          const match = d.match(/\d{4}-\d{2}-\d{2}/);
          const dateStr = match ? match[0] : d;
          return dateStr >= localTodayStr;
        });
      });

      console.log(`[Deck Fetch] DB Total: ${data?.length} | Swiped Excluded: ${swipedArray.length} | Active Deck: ${validEvents.length}`);
      setRawEvents(validEvents);
    } catch (err) {
      console.error('Error fetching discovery events:', err.message || err);
    } finally {
      setLoading(false);
    }
  }, [userHash, sessionKey]);

  useEffect(() => {
    const activeKey = getOrCreateClientId();
    setSessionKey(activeKey);

    const storedHash = localStorage.getItem('vivida_user_hash');
    if (storedHash) {
      setUserHash(storedHash);
    } else {
      setIsInitialPromptOpen(true);
    }
  }, []);

  useEffect(() => {
    fetchEvents();
  }, [fetchEvents]);

  const executeDeckReset = useCallback(async () => {
    setIsResetModalOpen(false);
    setLoading(true);

    try {
      localStorage.removeItem('vibe_history');
      localStorage.removeItem('vivida_bookmarks');
      setBookmarks([]);

      const activeUserIdentifier = userHash || sessionKey;
      if (supabase && activeUserIdentifier) {
        const { error } = await supabase
          .from('user_interactions')
          .delete()
          .eq('user_id', activeUserIdentifier);

        if (error) {
          console.error('Error clearing remote user interactions:', error.message);
        }
      }

      await fetchEvents();
    } catch (err) {
      console.error('Failed to reset discovery deck:', err);
    } finally {
      setLoading(false);
    }
  }, [userHash, sessionKey, fetchEvents]);

  const rankedEvents = useMemo(() => {
    if (!rawEvents || rawEvents.length === 0) return [];

    return [...rawEvents]
      .map((event) => {
        const matchScore = calculateEuclideanSimilarity(mood, event);
        return {
          ...event,
          matchScore,
        };
      })
      .sort((a, b) => b.matchScore - a.matchScore);
  }, [rawEvents, mood]);

  const handleInteraction = useCallback((type, event) => {
    if (!event) return;

    // 1. Remove event from active raw deck
    setRawEvents((prev) => prev.filter((e) => e.id !== event.id));

    // 2. Track bookmarks if positive vibe action
    if (type === 'interested' || type === 'totally_vibe' || type === 'maybe_later') {
      setBookmarks((prev) => {
        if (prev.some((b) => b.id === event.id)) return prev;
        return [...prev, event];
      });
    }

    // 3. Calculate new mood vector ONCE
    const nextMood = applyVectorDrift(event, type, mood);

    // 4. Update UI State synchronously
    setMood(nextMood);

    // 5. Persist to storage & DB
    recordSwipe(type, event, null, nextMood);

    if (supabase) {
      supabase.from('event_conversions').insert({
        session_id: sessionKey || 'anonymous_session',
        email_hash: userHash || null,
        event_id: event.id,
        interaction_type: type,
        target_energy: Number(nextMood.energy),
        target_social: Number(nextMood.social),
        target_novelty: Number(nextMood.novelty),
      }).then(({ error }) => {
        if (error) console.error('Error recording event conversion:', error.message);
      }).catch((err) => console.error('Async conversion insert error:', err));
    }
  }, [sessionKey, userHash, mood]);

  const activeTopEvent = rankedEvents[0];

  if (currentView === 'history') {
    return <HistoryPage onBack={() => setCurrentView('deck')} />;
  }

  return (
    <div className="h-dvh w-full bg-parchment-50 text-parchment-900 flex flex-col justify-between overflow-hidden pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)] relative">
      <Header 
        mood={mood}
        onChangeMood={handleMoodChange}
        onOpenSaved={() => setCurrentView('history')}
        onOpenHistory={() => setCurrentView('history')}
        bookmarkCount={bookmarks.length}
        hasUserHash={!!userHash}
      />

      <main className="flex-1 w-full max-w-md mx-auto px-4 flex flex-col justify-between overflow-hidden py-2">
        <div className="flex-1 flex flex-col justify-center items-center overflow-hidden">
          {loading ? (
            <div className="flex flex-col items-center justify-center text-parchment-800/60">
              <Sparkles className="w-8 h-8 animate-spin text-terracotta mb-2" />
              <span className="text-xs font-medium">Fetching hyper-local pulse...</span>
            </div>
          ) : (
            <SwipeDeck 
              events={rankedEvents} 
              mood={mood} 
              onSwipe={handleInteraction}
              onReset={() => setIsResetModalOpen(true)}
            />
          )}
        </div>

        {!loading && activeTopEvent && (
          <div className="flex items-center justify-center gap-6 py-2 shrink-0">
            <button
              onClick={() => handleInteraction('not_my_scene', activeTopEvent)}
              className="w-16 h-16 rounded-full bg-white border border-parchment-200 shadow-lg flex items-center justify-center text-rose-600 hover:bg-rose-50 active:scale-90 transition-transform"
              aria-label="Not My Scene"
            >
              <ThumbsDown className="w-7 h-7" />
            </button>

            <button
              onClick={() => handleInteraction('maybe_later', activeTopEvent)}
              className="w-12 h-12 rounded-full bg-white border border-parchment-200 shadow-md flex items-center justify-center text-amber-600 hover:bg-amber-50 active:scale-90 transition-transform"
              aria-label="Maybe Later"
            >
              <Heart className="w-5 h-5" />
            </button>

            <button
              onClick={() => handleInteraction('totally_vibe', activeTopEvent)}
              className="w-16 h-16 rounded-full bg-terracotta text-white shadow-xl flex items-center justify-center hover:bg-terracotta-hover active:scale-90 transition-transform"
              aria-label="Totally My Vibe"
            >
              <Flame className="w-7 h-7 fill-white" />
            </button>
          </div>
        )}
      </main>

      {isInitialPromptOpen && (
        <EmailPromptModal
          isOpen={isInitialPromptOpen}
          onClose={() => setIsInitialPromptOpen(false)}
          onSuccess={(hash) => {
            setUserHash(hash);
            localStorage.setItem('vivida_user_hash', hash);
            setIsInitialPromptOpen(false);
          }}
        />
      )}

      {/* CONFIRMATION DIALOG MODAL */}
      {isResetModalOpen && (
        <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4 animate-in fade-in duration-200">
          <div className="bg-white rounded-3xl max-w-sm w-full p-6 border border-parchment-200 shadow-2xl relative space-y-4 animate-in zoom-in-95 duration-200">
            <button
              onClick={() => setIsResetModalOpen(false)}
              className="absolute top-4 right-4 p-1.5 rounded-full text-parchment-400 hover:text-parchment-700 hover:bg-parchment-100 transition-colors"
              aria-label="Cancel"
            >
              <X className="w-4 h-4" />
            </button>

            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-2xl bg-amber-100 text-amber-700 flex items-center justify-center shrink-0">
                <AlertTriangle className="w-5 h-5" />
              </div>
              <div>
                <h3 className="font-serif font-bold text-base text-parchment-900">
                  Reset Event Deck?
                </h3>
                <p className="text-[11px] text-parchment-600 font-medium">
                  This action cannot be undone.
                </p>
              </div>
            </div>

            <p className="text-xs text-parchment-700 leading-relaxed bg-parchment-50 p-3 rounded-2xl border border-parchment-100">
              Clearing your deck will erase your saved swipe history and reload all 400+ local events back into your feed.
            </p>

            <div className="flex items-center justify-end gap-2 pt-2">
              <button
                onClick={() => setIsResetModalOpen(false)}
                className="px-4 py-2 rounded-xl text-xs font-semibold text-parchment-700 hover:bg-parchment-100 transition-colors"
              >
                Keep History
              </button>
              <button
                onClick={executeDeckReset}
                className="inline-flex items-center gap-1.5 bg-rose-600 hover:bg-rose-700 text-white text-xs font-bold px-4 py-2 rounded-xl transition-all shadow-md active:scale-95"
              >
                <RotateCcw className="w-3.5 h-3.5" />
                <span>Reset Deck</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}