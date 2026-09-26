import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { supabase } from './lib/supabaseClient.js';
import Header from './components/Header';
import SwipeDeck from './components/SwipeDeck';
import HistoryModal from './components/HistoryModal';
import EmailPromptModal from './components/EmailPromptModal';
import { ThumbsDown, Heart, Flame, Sparkles } from 'lucide-react';

export default function App() {
  const [rawEvents, setRawEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [sessionKey, setSessionKey] = useState('');
  const [userHash, setUserHash] = useState(null);

  // Modals state
  const [isHistoryOpen, setIsHistoryOpen] = useState(false);
  const [isInitialPromptOpen, setIsInitialPromptOpen] = useState(false);
  
  // Local Bookmarks state with persistence
  const [bookmarks, setBookmarks] = useState(() => {
    try {
      const saved = localStorage.getItem('vivida_bookmarks');
      return saved ? JSON.parse(saved) : [];
    } catch {
      return [];
    }
  });

  // Triaxial Mood Vector State
  const [mood, setMood] = useState({
    energy: 0.5,
    social: 0.5,
    novelty: 0.5,
  });

  // Persist Bookmarks updates to localStorage
  useEffect(() => {
    try {
      localStorage.setItem('vivida_bookmarks', JSON.stringify(bookmarks));
    } catch (err) {
      console.warn('Failed to persist bookmarks to localStorage:', err);
    }
  }, [bookmarks]);

  // Initialize Session Key, User Hash, and Initial Prompt on Mount
  useEffect(() => {
    let activeKey = localStorage.getItem('vivida_session_key');
    if (!activeKey) {
      activeKey = crypto.randomUUID();
      localStorage.setItem('vivida_session_key', activeKey);
    }
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

  // Distance Sorting via Euclidean Triaxial Mood Vectors
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

  // Handle User Swipes and Log Interactions to Supabase
  const handleInteraction = useCallback(async (type, event) => {
    if (!event) return;

    setRawEvents((prev) => prev.filter((e) => e.id !== event.id));

    if (type === 'interested' || type === 'totally_vibe' || type === 'maybe_later') {
      setBookmarks((prev) => {
        if (prev.some((b) => b.id === event.id)) return prev;
        return [...prev, event];
      });
    }

    const payload = {
      session_id: sessionKey,
      email_hash: userHash || null,
      event_id: event.id,
      interaction_type: type,
      target_energy: mood.energy,
      target_social: mood.social,
      target_novelty: mood.novelty,
    };

    try {
      const { data, error } = await supabase
        .from('event_conversions')
        .insert(payload)
        .select();

      if (error) {
        console.error('Supabase Insert Error:', error.message, error.details, error.hint);
      } else {
        console.log('Successfully inserted conversion record:', data);
      }
    } catch (err) {
      console.error('Unexpected error recording interaction:', err);
    }
  }, [sessionKey, userHash, mood]);

  const activeTopEvent = rankedEvents[0];

  return (
    <div className="min-h-screen bg-parchment-50 text-parchment-900 flex flex-col font-sans">
      {/* Header with Connected Props */}
      <Header 
        mood={mood}
        onChangeMood={setMood}
        onOpenSaved={() => setIsHistoryOpen(true)}
        onOpenHistory={() => setIsHistoryOpen(true)}
        bookmarkCount={bookmarks.length}
        hasUserHash={!!userHash}
      />

      <main className="flex-1 max-w-md w-full mx-auto px-4 py-2 flex flex-col justify-between">
        <div className="my-auto">
          {loading ? (
            <div className="h-[440px] flex flex-col items-center justify-center text-parchment-800/60">
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
          <div className="flex items-center justify-center gap-4 my-4">
            <button
              onClick={() => handleInteraction('not_my_scene', activeTopEvent)}
              className="w-14 h-14 rounded-full bg-white border border-parchment-200 shadow-md flex items-center justify-center text-rose-600 hover:bg-rose-50 transition-colors active:scale-95"
              title="Not My Scene"
              aria-label="Not My Scene"
            >
              <ThumbsDown className="w-6 h-6" />
            </button>

            <button
              onClick={() => handleInteraction('maybe_later', activeTopEvent)}
              className="w-12 h-12 rounded-full bg-white border border-parchment-200 shadow-md flex items-center justify-center text-amber-600 hover:bg-amber-50 transition-colors active:scale-95"
              title="Maybe Later"
              aria-label="Maybe Later"
            >
              <Heart className="w-5 h-5" />
            </button>

            <button
              onClick={() => handleInteraction('totally_vibe', activeTopEvent)}
              className="w-14 h-14 rounded-full bg-terracotta text-white shadow-lg flex items-center justify-center hover:bg-terracotta-hover transition-colors active:scale-95"
              title="Totally My Vibe"
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
          onClose={() => setIsInitialPromptOpen(false)}
          onSuccess={(hash) => {
            setUserHash(hash);
            localStorage.setItem('vivida_user_hash', hash);
            setIsInitialPromptOpen(false);
          }}
        />
      )}

      <HistoryModal
        isOpen={isHistoryOpen}
        onClose={() => setIsHistoryOpen(false)}
        userHash={userHash}
        bookmarks={bookmarks}
        onUserHashCreated={(newHash) => {
          setUserHash(newHash);
          localStorage.setItem('vivida_user_hash', newHash);
        }}
      />
    </div>
  );
}