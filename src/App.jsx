// src/App.jsx
import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { supabase } from './lib/supabaseClient.js';
import Header from './components/Header';
import SwipeDeck from './components/SwipeDeck';
import ContinuousFeed from './components/ContinuousFeed';
import EmailPromptModal from './components/EmailPromptModal';
import { ThumbsDown, Heart, Flame, Sparkles, RotateCcw, AlertTriangle, X, Layers, ListFilter, Undo2 } from 'lucide-react';
import HistoryPage from './components/HistoryPage';
import { getCurrentMood, recordSwipe, setCurrentMood, applyVectorDrift, getFutureUserInteractions, getUserHistory } from './services/historyService';
import { getOrCreateClientId } from './utils/session';

const calculateEuclideanSimilarity = (userMood, event) => {
  if (!userMood || !event) return 0;

  const uE = Number(userMood.energy ?? 0.5);
  const uS = Number(userMood.social ?? 0.5);
  const uN = Number(userMood.novelty ?? 0.5);

  const eE = Number(event.energy ?? event.energy_vector ?? 0.5);
  const eS = Number(event.social ?? event.social_vector ?? 0.5);
  const eN = Number(event.novelty ?? event.novelty_vector ?? 0.5);

  const distSquared = (uE - eE) ** 2 + (uS - eS) ** 2 + (uN - eN) ** 2;
  const SIGMA = 0.5;
  const similarityScore = Math.exp(-distSquared / (2 * (SIGMA ** 2)));

  return Number(similarityScore.toFixed(4));
};

const getEarliestEventDate = (event, todayStr, extractDatesFn) => {
  if (!event || !event.schedule) return '9999-99-99';

  const dateList = extractDatesFn(event.schedule);
  if (dateList.length === 0) return '9999-99-99';

  const validDates = dateList
    .map((d) => {
      const match = String(d).match(/\d{4}-\d{2}-\d{2}/);
      return match ? match[0] : String(d);
    })
    .filter((d) => d >= todayStr)
    .sort();

  return validDates[0] || '9999-99-99';
};

export default function App() {
  const [rawEvents, setRawEvents] = useState([]);
  const [allFetchedEvents, setAllFetchedEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [sessionKey, setSessionKey] = useState('');
  const [userHash, setUserHash] = useState(null);
  const [currentView, setCurrentView] = useState('deck'); 
  const [deckMode, setDeckMode] = useState('swipe');
  const [isInitialPromptOpen, setIsInitialPromptOpen] = useState(false);
  const [isResetModalOpen, setIsResetModalOpen] = useState(false);
  const [isLocalResetModalOpen, setIsLocalResetModalOpen] = useState(false);

  const [interactionStack, setInteractionStack] = useState([]);

  // Explicit count structure when HistoryPage reports expanded card totals
  const [historyRenderedCounts, setHistoryRenderedCounts] = useState(null);

  const [bookmarks, setBookmarks] = useState(() => {
    try {
      const saved = localStorage.getItem('vivida_bookmarks');
      return saved ? JSON.parse(saved) : [];
    } catch {
      return [];
    }
  });

  const [mood, setMood] = useState(() => getCurrentMood());

  const handleMoodChange = useCallback((newMood) => {
    const updated = setCurrentMood(newMood);
    setMood(updated);
  }, []);

  // Sync bookmarks state with full combined remote and local history
  const refreshHistoryCount = useCallback(async (hash = null) => {
    const activeHash = hash || userHash || localStorage.getItem('vivida_user_hash');
    const historyItems = await getUserHistory(activeHash);
    
    if (historyItems && Array.isArray(historyItems)) {
      const savedEvents = historyItems.map((item) => item.events || item.event_snapshot).filter(Boolean);
      
      setBookmarks((prev) => {
        const combinedMap = new Map();
        
        // Load existing local state
        prev.forEach((item) => {
          const rawId = item?.id || item?.event_id;
          if (rawId) {
            combinedMap.set(String(rawId).trim().toLowerCase(), item);
          }
        });

        // Merge remote items over local state
        savedEvents.forEach((item) => {
          const rawId = item?.id || item?.event_id;
          if (rawId) {
            combinedMap.set(String(rawId).trim().toLowerCase(), item);
          }
        });

        return Array.from(combinedMap.values());
      });
    }
  }, [userHash]);

  useEffect(() => {
    try {
      localStorage.setItem('vivida_bookmarks', JSON.stringify(bookmarks));
    } catch (err) {
      console.warn('Failed to persist bookmarks to localStorage:', err);
    }
  }, [bookmarks]);

  const extractDatesFromSchedule = useCallback((schedule) => {
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
  }, []);

  const fetchEvents = useCallback(async (overrideHash = null) => {
    setLoading(true);
    try {
      if (!supabase) return;

      const localSwipedIds = new Set();
      const activeUserIdentifier = overrideHash || userHash || localStorage.getItem('vivida_user_hash') || sessionKey || getOrCreateClientId();

      // 1. Read local history IDs & normalize
      try {
        const localHistory = JSON.parse(localStorage.getItem('vibe_history') || '[]');
        if (Array.isArray(localHistory)) {
          localHistory.forEach((item) => {
            if (item?.event_id) localSwipedIds.add(String(item.event_id).trim().toLowerCase());
          });
        }
      } catch (err) {
        console.warn('Failed to read vibe_history from localStorage:', err);
      }

      // 2. Fetch past choices from Supabase using active user identifier
      if (activeUserIdentifier) {
        const futureInteractions = await getFutureUserInteractions(activeUserIdentifier);
        if (futureInteractions && futureInteractions.length > 0) {
          futureInteractions.forEach((item) => {
            if (item?.event_id) localSwipedIds.add(String(item.event_id).trim().toLowerCase());
          });
        }
      }

      const swipedArray = Array.from(localSwipedIds);

      let query = supabase
        .from('events')
        .select('*')
        .range(0, 999);

      if (swipedArray.length > 0) {
        query = query.not('id', 'in', `(${swipedArray.join(',')})`);
      }

      const { data, error } = await query;
      if (error) throw error;

      const now = new Date();
      const localTodayStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;

      const validEvents = (data || []).filter((event) => {
        const eventIdStr = String(event.id).trim().toLowerCase();
        if (localSwipedIds.has(eventIdStr)) return false;

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
      setAllFetchedEvents(data || []);
    } catch (err) {
      console.error('Error fetching discovery events:', err.message || err);
    } finally {
      setLoading(false);
    }
  }, [userHash, sessionKey, extractDatesFromSchedule]);

  useEffect(() => {
    const activeKey = getOrCreateClientId();
    setSessionKey(activeKey);

    const storedHash = localStorage.getItem('vivida_user_hash');
    if (storedHash) {
      setUserHash(storedHash);
      refreshHistoryCount(storedHash);
    } else {
      setIsInitialPromptOpen(true);
    }
  }, [refreshHistoryCount]);

  useEffect(() => {
    fetchEvents();
  }, [fetchEvents]);

  const handleExhaustedDeckRecycle = useCallback(() => {
    if (allFetchedEvents.length > 0) {
      setRawEvents([...allFetchedEvents]);
    } else {
      fetchEvents();
    }
  }, [allFetchedEvents, fetchEvents]);

  useEffect(() => {
    if (!loading && rawEvents.length === 0 && interactionStack.length > 0) {
      handleExhaustedDeckRecycle();
    }
  }, [rawEvents, loading, interactionStack, handleExhaustedDeckRecycle]);

  const executeDeckReset = useCallback(async () => {
    setIsResetModalOpen(false);
    setLoading(true);

    try {
      localStorage.removeItem('vibe_history');
      localStorage.removeItem('vivida_bookmarks');
      setBookmarks([]);
      setHistoryRenderedCounts({ vibeCount: 0, savedCount: 0 });
      setInteractionStack([]);

      const activeUserIdentifier = userHash || localStorage.getItem('vivida_user_hash') || sessionKey;
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

  const handleResetLocalHistory = useCallback(() => {
    localStorage.removeItem('vibe_history');
    localStorage.removeItem('vivida_user_mood_vector');
    localStorage.removeItem('vivida_bookmarks');

    const defaultMood = { energy: 0.5, social: 0.5, novelty: 0.5 };
    setCurrentMood(defaultMood);
    setMood(defaultMood);

    setBookmarks([]);
    setHistoryRenderedCounts({ vibeCount: 0, savedCount: 0 });
    setInteractionStack([]);
    setIsLocalResetModalOpen(false);

    fetchEvents();
  }, [fetchEvents]);

  const rankedEvents = useMemo(() => {
    if (!rawEvents || rawEvents.length === 0) return [];

    const now = new Date();
    const localTodayStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;

    return [...rawEvents]
      .map((event) => {
        const matchScore = calculateEuclideanSimilarity(mood, event);
        const earliestDate = getEarliestEventDate(event, localTodayStr, extractDatesFromSchedule);
        return {
          ...event,
          matchScore,
          earliestDate,
        };
      })
      .sort((a, b) => {
        if (a.earliestDate !== b.earliestDate) {
          return a.earliestDate.localeCompare(b.earliestDate);
        }
        return b.matchScore - a.matchScore;
      });
  }, [rawEvents, mood, extractDatesFromSchedule]);

  const handleInteraction = useCallback(async (type, event) => {
    if (!event) return;

    const activeHash = userHash || localStorage.getItem('vivida_user_hash') || sessionKey;

    setInteractionStack((prev) => [
      ...prev,
      {
        event,
        actionType: type,
        previousMood: { ...mood },
      },
    ]);

    setRawEvents((prev) => prev.filter((e) => String(e.id).trim().toLowerCase() !== String(event.id).trim().toLowerCase()));

    if (['interested', 'totally_vibe', 'maybe_later'].includes(type)) {
      setBookmarks((prev) => {
        const eventIdStr = String(event.id).trim().toLowerCase();
        if (prev.some((b) => String(b.id || b.event_id).trim().toLowerCase() === eventIdStr)) {
          return prev;
        }
        return [event, ...prev];
      });
    }

    const nextMood = applyVectorDrift(event, type, mood);
    setMood(nextMood);

    recordSwipe(type, event, activeHash, nextMood);

    if (supabase) {
      supabase.from('event_conversions').insert({
        session_id: sessionKey || 'anonymous_session',
        email_hash: userHash || null,
        event_id: String(event.id),
        interaction_type: type,
        target_energy: Number(nextMood.energy),
        target_social: Number(nextMood.social),
        target_novelty: Number(nextMood.novelty),
      }).catch((err) => console.error('Async conversion insert error:', err));
    }
  }, [sessionKey, userHash, mood]);

  const handleUndo = useCallback(async () => {
    if (interactionStack.length === 0) return;

    const lastInteraction = interactionStack[interactionStack.length - 1];
    const { event, previousMood, actionType } = lastInteraction;

    setRawEvents((prev) => [event, ...prev]);

    if (previousMood) {
      const restoredMood = setCurrentMood(previousMood);
      setMood(restoredMood);
    }

    if (['interested', 'totally_vibe', 'maybe_later'].includes(actionType)) {
      setBookmarks((prev) => prev.filter((b) => String(b.id || b.event_id).trim().toLowerCase() !== String(event.id).trim().toLowerCase()));
    }

    try {
      const history = JSON.parse(localStorage.getItem('vibe_history') || '[]');
      const filteredHistory = history.filter((item) => String(item.event_id).trim().toLowerCase() !== String(event.id).trim().toLowerCase());
      localStorage.setItem('vibe_history', JSON.stringify(filteredHistory));
    } catch (err) {
      console.warn('Failed to revert vibe_history in localStorage:', err);
    }

    const activeUserIdentifier = userHash || localStorage.getItem('vivida_user_hash') || sessionKey;
    if (supabase && activeUserIdentifier) {
      await supabase
        .from('user_interactions')
        .delete()
        .eq('user_id', activeUserIdentifier)
        .eq('event_id', String(event.id));
    }

    setInteractionStack((prev) => prev.slice(0, -1));
  }, [interactionStack, sessionKey, userHash]);

  const activeTopEvent = rankedEvents[0];

  // Derive counts directly from HistoryPage when reported, or fallback to local bookmarks state
  const effectiveVibeCount = historyRenderedCounts ? historyRenderedCounts.vibeCount : bookmarks.length;
  const effectiveSavedCount = historyRenderedCounts ? historyRenderedCounts.savedCount : 0;

  if (currentView === 'history') {
    return (
      <HistoryPage 
        onBack={() => setCurrentView('deck')} 
        onCountUpdate={(counts) => setHistoryRenderedCounts(counts)}
      />
    );
  }

  return (
    <div className="h-dvh w-full bg-parchment-50 text-parchment-900 flex flex-col justify-between overflow-hidden pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)] relative">
      <Header 
        mood={mood}
        onChangeMood={handleMoodChange}
        onOpenSaved={() => setCurrentView('history')}
        onOpenHistory={() => setCurrentView('history')}
        onResetLocalHistory={() => setIsLocalResetModalOpen(true)}
        vibeCount={effectiveVibeCount}
        savedCount={effectiveSavedCount}
        hasUserHash={!!userHash}
      />

      <main className="flex-1 w-full max-w-md mx-auto px-4 flex flex-col justify-between overflow-hidden py-2">
        <div className="flex items-center gap-2 mb-2 shrink-0">
          <div className="flex-1 flex items-center justify-center p-1 bg-parchment-200/60 rounded-2xl border border-parchment-200/80">
            <button
              onClick={() => setDeckMode('swipe')}
              className={`flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-xl text-xs font-bold transition-all ${
                deckMode === 'swipe'
                  ? 'bg-white text-parchment-900 shadow-xs'
                  : 'text-parchment-700 hover:text-parchment-900'
              }`}
            >
              <Layers className="w-3.5 h-3.5 text-terracotta" />
              <span>Swipe</span>
            </button>

            <button
              onClick={() => setDeckMode('feed')}
              className={`flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-xl text-xs font-bold transition-all ${
                deckMode === 'feed'
                  ? 'bg-white text-parchment-900 shadow-xs'
                  : 'text-parchment-700 hover:text-parchment-900'
              }`}
            >
              <ListFilter className="w-3.5 h-3.5 text-terracotta" />
              <span>Search ({rankedEvents.length})</span>
            </button>
          </div>

          <button
            onClick={handleUndo}
            disabled={interactionStack.length === 0}
            className={`p-2.5 rounded-2xl border transition-all flex items-center justify-center shrink-0 ${
              interactionStack.length > 0
                ? 'bg-white border-parchment-200 text-terracotta shadow-2xs hover:bg-parchment-100 active:scale-90'
                : 'bg-parchment-100 border-parchment-200/50 text-parchment-400 cursor-not-allowed opacity-50'
            }`}
            title="Undo last choice"
            aria-label="Undo last choice"
          >
            <Undo2 className="w-4 h-4" />
          </button>
        </div>

        <div className="flex-1 flex flex-col justify-center items-center overflow-hidden min-h-0">
          {loading ? (
            <div className="flex flex-col items-center justify-center text-parchment-800/60">
              <Sparkles className="w-8 h-8 animate-spin text-terracotta mb-2" />
              <span className="text-xs font-medium">Fetching hyper-local pulse...</span>
            </div>
          ) : deckMode === 'swipe' ? (
            <SwipeDeck 
              events={rankedEvents} 
              mood={mood} 
              onSwipe={handleInteraction}
              onReset={() => setIsResetModalOpen(true)}
            />
          ) : (
            <ContinuousFeed
              events={rankedEvents}
              mood={mood}
              onSwipe={handleInteraction}
              onReset={() => setIsResetModalOpen(true)}
              onUndo={handleUndo}
              canUndo={interactionStack.length > 0}
            />
          )}
        </div>

        {!loading && activeTopEvent && deckMode === 'swipe' && (
          <div className="flex items-center justify-center gap-4 py-2 shrink-0">
            <button
              onClick={() => handleInteraction('not_my_scene', activeTopEvent)}
              className="w-14 h-14 rounded-full bg-white border border-parchment-200 shadow-lg flex items-center justify-center text-rose-600 hover:bg-rose-50 active:scale-90 transition-transform"
              aria-label="Not My Scene"
            >
              <ThumbsDown className="w-6 h-6" />
            </button>

            <button
              onClick={handleUndo}
              disabled={interactionStack.length === 0}
              className={`w-10 h-10 rounded-full border shadow-md flex items-center justify-center transition-transform ${
                interactionStack.length > 0
                  ? 'bg-white border-parchment-200 text-terracotta hover:bg-parchment-50 active:scale-90'
                  : 'bg-parchment-100 border-parchment-200/50 text-parchment-300 cursor-not-allowed'
              }`}
              aria-label="Undo Last Action"
            >
              <Undo2 className="w-4 h-4" />
            </button>

            <button
              onClick={() => handleInteraction('maybe_later', activeTopEvent)}
              className="w-10 h-10 rounded-full bg-white border border-parchment-200 shadow-md flex items-center justify-center text-amber-600 hover:bg-amber-50 active:scale-90 transition-transform"
              aria-label="Maybe Later"
            >
              <Heart className="w-4 h-4" />
            </button>

            <button
              onClick={() => handleInteraction('totally_vibe', activeTopEvent)}
              className="w-14 h-14 rounded-full bg-terracotta text-white shadow-xl flex items-center justify-center hover:bg-terracotta-hover active:scale-90 transition-transform"
              aria-label="Totally My Vibe"
            >
              <Flame className="w-6 h-6 fill-white" />
            </button>
          </div>
        )}
      </main>

      {isInitialPromptOpen && (
        <EmailPromptModal
          isOpen={isInitialPromptOpen}
          onClose={() => {
            setIsInitialPromptOpen(false);
            fetchEvents();
          }}
          onSuccess={async (hash) => {
            localStorage.setItem('vivida_user_hash', hash);
            setUserHash(hash);
            setIsInitialPromptOpen(false);
            await refreshHistoryCount(hash);
            await fetchEvents(hash);
          }}
        />
      )}

      {/* FULL DECK RESET CONFIRMATION MODAL */}
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
              Clearing your deck will erase your saved swipe history and reload all local events back into your feed.
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

      {/* LOCAL VIBE RESET CONFIRMATION MODAL */}
      {isLocalResetModalOpen && (
        <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4 animate-in fade-in duration-200">
          <div className="bg-white rounded-3xl max-w-sm w-full p-6 border border-parchment-200 shadow-2xl relative space-y-4 animate-in zoom-in-95 duration-200">
            <button
              onClick={() => setIsLocalResetModalOpen(false)}
              className="absolute top-4 right-4 p-1.5 rounded-full text-parchment-400 hover:text-parchment-700 hover:bg-parchment-100 transition-colors"
              aria-label="Cancel"
            >
              <X className="w-4 h-4" />
            </button>

            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-2xl bg-amber-100 text-amber-700 flex items-center justify-center shrink-0">
                <RotateCcw className="w-5 h-5" />
              </div>
              <div>
                <h3 className="font-serif font-bold text-base text-parchment-900">
                  Reset Local Vibe?
                </h3>
                <p className="text-[11px] text-parchment-600 font-medium">
                  Clears local deck state & saved vectors
                </p>
              </div>
            </div>

            <p className="text-xs text-parchment-700 leading-relaxed bg-parchment-50 p-3 rounded-2xl border border-parchment-100">
              This resets your local mood preferences back to baseline and reloads the event deck. Your logged history in the database will remain intact.
            </p>

            <div className="flex items-center justify-end gap-2 pt-2">
              <button
                onClick={() => setIsLocalResetModalOpen(false)}
                className="px-4 py-2 rounded-xl text-xs font-semibold text-parchment-700 hover:bg-parchment-100 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleResetLocalHistory}
                className="inline-flex items-center gap-1.5 bg-terracotta hover:bg-terracotta-hover text-white text-xs font-bold px-4 py-2 rounded-xl transition-all shadow-md active:scale-95"
              >
                <RotateCcw className="w-3.5 h-3.5" />
                <span>Reset Vibe</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}