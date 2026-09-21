import React, { useState, useEffect } from 'react';
import { createClient } from '@supabase/supabase-js';
import Header from './components/Header';
import MoodSliders from './components/MoodSliders';
import SwipeDeck from './components/SwipeDeck';
import { ThumbsDown, Heart, Flame, Share2, Sparkles } from 'lucide-react';

// Initialize Supabase Client from environment
const supabaseUrl = import.meta.env.VITE_SUPABASE_URL || '';
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY || '';
const supabase = createClient(supabaseUrl, supabaseAnonKey);

export default function App() {
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [sessionKey, setSessionKey] = useState('');
  const [bookmarks, setBookmarks] = useState([]);
  
  // Triaxial Mood Vector State
  const [mood, setMood] = useState({
    energy: 0.5,
    social: 0.5,
    novelty: 0.5,
  });

  // Initialize Ephemeral Session Key (6-Month Lifecycle)
  useEffect(() => {
    let activeKey = localStorage.getItem('vivida_session_key');
    if (!activeKey) {
      activeKey = 'sess_' + crypto.randomUUID();
      localStorage.setItem('vivida_session_key', activeKey);
    }
    setSessionKey(activeKey);
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

    // Local state optimistic update
    setEvents((prev) => prev.filter((e) => e.id !== event.id));
    if (type === 'interested' || type === 'totally_vibe') {
      setBookmarks((prev) => [...prev, event]);
    }

    // Persist interaction to database
    try {
      await supabase.from('event_conversions').insert({
        session_id: null, // Resolves via session key mapping procedure if needed
        event_id: event.id,
        interaction_type: type,
        target_energy: mood.energy,
        target_social: mood.social,
        target_novelty: mood.novelty,
      });
    } catch (err) {
      console.warn('Conversion logging notice:', err.message);
    }
  };

  return (
    <div className="min-h-screen bg-parchment-50 text-parchment-900 flex flex-col font-sans">
      {/* Header */}
      <Header 
        onOpenBookmarks={() => alert(`Saved events: ${bookmarks.length}`)}
        onOpenHistory={() => alert('History reconstruction triggered.')}
        bookmarkCount={bookmarks.length}
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
            <SwipeDeck events={events} mood={mood} onSwipe={handleInteraction} />
          )}
        </div>

        {/* 3-Button Interaction Footer */}
        {events.length > 0 && (
          <div className="flex items-center justify-center gap-4 my-4">
            <button
              onClick={() => handleInteraction('not_my_scene', events[0])}
              className="w-14 h-14 rounded-full bg-white border border-parchment-200 shadow-md flex items-center justify-center text-rose-600 hover:bg-rose-50 transition-colors"
              title="Not My Scene"
            >
              <ThumbsDown className="w-6 h-6" />
            </button>

            <button
              onClick={() => handleInteraction('interested', events[0])}
              className="w-12 h-12 rounded-full bg-white border border-parchment-200 shadow-md flex items-center justify-center text-amber-600 hover:bg-amber-50 transition-colors"
              title="Interested"
            >
              <Heart className="w-5 h-5" />
            </button>

            <button
              onClick={() => handleInteraction('totally_vibe', events[0])}
              className="w-14 h-14 rounded-full bg-terracotta text-white shadow-lg flex items-center justify-center hover:bg-terracotta-hover transition-colors"
              title="Totally My Vibe"
            >
              <Flame className="w-6 h-6 fill-white" />
            </button>
          </div>
        )}
      </main>
    </div>
  );
}