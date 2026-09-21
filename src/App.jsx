import React, { useState, useEffect, useMemo } from 'react';
import { supabase } from './lib/supabase';
import Header from './components/Header';
import MoodSliders from './components/MoodSliders';
import SwipeDeck from './components/SwipeDeck';
import HistoryModal from './components/HistoryModal';
import EmailPromptModal from './components/EmailPromptModal';
import { ThumbsDown, Heart, Flame, Sparkles } from 'lucide-react';

export default function App() {
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [sessionKey, setSessionKey] = useState('');
  const [userHash, setUserHash] = useState(null);
  
  // Modals state
  const [isHistoryOpen, setIsHistoryOpen] = useState(false);
  const [isInitialPromptOpen, setIsInitialPromptOpen] = useState(false);
  const [bookmarks, setBookmarks] = useState([]);
  
  // Triaxial Mood Vector State
  const [mood, setMood] = useState({
    energy: 0.5,
    social: 0.5,
    novelty: 0.5,
  });

  // Initialize Session Key, User Hash, and Initial Prompt on Mount
  useEffect(() => {
    let activeKey = localStorage.getItem('vivida_session_key');
    if (!activeKey) {
      activeKey = 'sess_' + crypto.randomUUID();
      localStorage.setItem('vivida_session_key', activeKey);
    }
    setSessionKey(activeKey);

    const storedHash = localStorage.getItem('vivida_user_hash');
    if (storedHash) {
      setUserHash(storedHash);
    } else {
      // Prompt upfront on initial session load if no hash exists
      setIsInitialPromptOpen(true);
    }

    fetchEvents();
  }, []);

  // Fetch Ingested Events from Supabase
  const fetchEvents = async () => {
    setLoading(true);
    try {
      const { data, error } = await supabase
        .from('events')
        .select('*')
        .gte('start_time', new Date().toISOString())
        .order('start_time', { ascending: true })
        .limit(30);

      if (error) throw error;
      setEvents(data || []);
    } catch (err) {
      console.error('Error fetching events:', err.message);
    } finally {
      setLoading(false);
    }
  };

  // Handle User Swipes and Log Interactions to Supabase
  const handleInteraction = async (type, event) => {
    if (!event) return;

    // 1. Optimistic UI Update: Remove card from stack
    setEvents((prev) => prev.filter((e) => e.id !== event.id));

    // 2. Add to local Bookmarks state if it's a positive or 'maybe' vibe
    if (type === 'interested' || type === 'totally_vibe' || type === 'maybe_later') {
      setBookmarks((prev) => [...prev, event]);
    }

    // 3. Persist interaction & slider state vectors to database
    try {
      await supabase.from('event_conversions').insert({
        session_id: sessionKey,
        email_hash: userHash || null,
        event_id: event.id,
        interaction_type: type, // 'totally_vibe' | 'maybe_later' | 'not_my_scene'
        target_energy: mood.energy,
        target_social: mood.social,
        target_novelty: mood.novelty,
      });
    } catch (err) {
      console.warn('Conversion logging notice:', err.message);
    }
  };

  // Distance Sorting via Euclidean Triaxial Mood Vectors
  const rankedEvents = useMemo(() => {
    if (!events || events.length === 0) return [];

    return [...events].sort((a, b) => {
      const eA = a.energy ?? a.energy_vector ?? 0.5;
      const sA = a.social ?? a.social_vector ?? 0.5;
      const nA = a.novelty ?? a.novelty_vector ?? 0.5;

      const eB = b.energy ?? b.energy_vector ?? 0.5;
      const sB = b.social ?? b.social_vector ?? 0.5;
      const nB = b.novelty ?? b.novelty_vector ?? 0.5;

      const distA = Math.sqrt(
        Math.pow(mood.energy - eA, 2) +
        Math.pow(mood.social - sA, 2) +
        Math.pow(mood.novelty - nA, 2)
      );

      const distB = Math.sqrt(
        Math.pow(mood.energy - eB, 2) +
        Math.pow(mood.social - sB, 2) +
        Math.pow(mood.novelty - nB, 2)
      );

      return distA - distB;
    });
  }, [events, mood]);

  return (
    <div className="min-h-screen bg-parchment-50 text-parchment-900 flex flex-col font-sans">
      {/* Header with Modal Trigger */}
      <Header 
        onOpenBookmarks={() => alert(`Saved events: ${bookmarks.length}`)}
        onOpenHistory={() => setIsHistoryOpen(true)}
        bookmarkCount={bookmarks.length}
        hasUserHash={!!userHash}
      />

      {/* Main Container */}
      <main className="flex-1 max-w-md w-full mx-auto px-4 py-2 flex flex-col justify-between">
        {/* Mood Slider Control Panel */}
        <MoodSliders mood={mood} onChange={setMood} />

        {/* Swipe Card Area */}
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
              onSwipe={(type, event) => handleInteraction(type, event)} 
            />
          )}
        </div>

        {/* 3-Button Interaction Footer */}
        {rankedEvents.length > 0 && (
          <div className="flex items-center justify-center gap-4 my-4">
            <button
              onClick={() => handleInteraction('not_my_scene', rankedEvents[0])}
              className="w-14 h-14 rounded-full bg-white border border-parchment-200 shadow-md flex items-center justify-center text-rose-600 hover:bg-rose-50 transition-colors"
              title="Not My Scene"
            >
              <ThumbsDown className="w-6 h-6" />
            </button>

            <button
              onClick={() => handleInteraction('interested', rankedEvents[0])}
              className="w-12 h-12 rounded-full bg-white border border-parchment-200 shadow-md flex items-center justify-center text-amber-600 hover:bg-amber-50 transition-colors"
              title="Interested"
            >
              <Heart className="w-5 h-5" />
            </button>

            <button
              onClick={() => handleInteraction('totally_vibe', rankedEvents[0])}
              className="w-14 h-14 rounded-full bg-terracotta text-white shadow-lg flex items-center justify-center hover:bg-terracotta-hover transition-colors"
              title="Totally My Vibe"
            >
              <Flame className="w-6 h-6 fill-white" />
            </button>
          </div>
        )}
      </main>

      {/* Upfront Initial Email Onboarding Modal */}
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

      {/* Zero-PII History & Export Modal Container */}
      <HistoryModal
        isOpen={isHistoryOpen}
        onClose={() => setIsHistoryOpen(false)}
        userHash={userHash}
        onUserHashCreated={(newHash) => {
          setUserHash(newHash);
          localStorage.setItem('vivida_user_hash', newHash);
        }}
      />
    </div>
  );
}