import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { supabase } from './lib/supabaseClient.js';
import Header from './components/Header';
import SwipeDeck from './components/SwipeDeck';
import EmailPromptModal from './components/EmailPromptModal';
import { ThumbsDown, Heart, Flame, Sparkles } from 'lucide-react';
import HistoryPage from './components/HistoryPage';
import { getCurrentMood, recordSwipe, setCurrentMood, applyVectorDrift } from './services/historyService';
import { getOrCreateClientId } from './utils/session';

export default function App() {
  const [rawEvents, setRawEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [sessionKey, setSessionKey] = useState('');
  const [userHash, setUserHash] = useState(null);
  const [currentView, setCurrentView] = useState('deck'); // 'deck' | 'history'
  const [isInitialPromptOpen, setIsInitialPromptOpen] = useState(false);

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

  useEffect(() => {
    const activeKey = getOrCreateClientId();
    setSessionKey(activeKey);

    const storedHash = localStorage.getItem('vivida_user_hash');
    if (storedHash) {
      setUserHash(storedHash);
    } else {
      setIsInitialPromptOpen(true);
    }

    fetchEvents();
  }, []);

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

  const fetchEvents = async () => {
    setLoading(true);
    try {
      if (!supabase) return;
      const { data, error } = await supabase
        .from('events')
        .select('*')
        .limit(100);

      if (error) throw error;

      const now = new Date();
      const localTodayStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;

      const validEvents = (data || []).filter((event) => {
        if (!event.schedule) return true;
        const dateList = extractDatesFromSchedule(event.schedule);
        if (dateList.length === 0) return true;

        return dateList.some((d) => {
          const match = d.match(/\d{4}-\d{2}-\d{2}/);
          const dateStr = match ? match[0] : d;
          return dateStr >= localTodayStr;
        });
      });

      setRawEvents(validEvents);
    } catch (err) {
      console.error('Error fetching events:', err.message || err);
    } finally {
      setLoading(false);
    }
  };

  const rankedEvents = useMemo(() => {
    if (!rawEvents || rawEvents.length === 0) return [];

    return [...rawEvents].sort((a, b) => {
      const eA = a.energy ?? a.energy_vector ?? 0.5;
      const sA = a.social ?? a.social_vector ?? 0.5;
      const nA = a.novelty ?? a.novelty_vector ?? 0.5;

      const eB = b.energy ?? b.energy_vector ?? 0.5;
      const sB = b.social ?? b.social_vector ?? 0.5;
      const nB = b.novelty ?? b.novelty_vector ?? 0.5;

      const distA = Math.hypot(mood.energy - eA, mood.social - sA, mood.novelty - nA);
      const distB = Math.hypot(mood.energy - eB, mood.social - sB, mood.novelty - nB);

      return distA - distB;
    });
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

    // 5. Persist to storage & DB (pass nextMood so it doesn't re-calculate)
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
    <div className="h-dvh w-full bg-parchment-50 text-parchment-900 flex flex-col justify-between overflow-hidden pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)]">
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
    </div>
  );
}