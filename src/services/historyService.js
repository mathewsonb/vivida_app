// src/services/historyService.js

import { supabase } from '../lib/supabaseClient'; // Adjust path if your client file is located elsewhere
import { getOrCreateClientId } from '../utils/session';

/**
 * Log a card swipe action to Supabase
 */
export async function recordSwipe(action, event) {
  const clientId = getOrCreateClientId();

  // Save to LocalStorage for instant fallback
  try {
    const localHistory = JSON.parse(localStorage.getItem('vibe_history') || '[]');
    const existingIndex = localHistory.findIndex(item => item.event_id === event.id);
    const entry = {
      id: crypto.randomUUID(),
      user_id: clientId,
      event_id: event.id,
      action: action,
      created_at: new Date().toISOString(),
      events: event
    };

    if (existingIndex >= 0) {
      localHistory[existingIndex] = entry;
    } else {
      localHistory.unshift(entry);
    }
    localStorage.setItem('vibe_history', JSON.stringify(localHistory));
  } catch (err) {
    console.warn('LocalStorage save failed:', err);
  }

  // Persist to Supabase DB
  if (supabase) {
    const { error } = await supabase.from('user_interactions').upsert(
      {
        user_id: clientId,
        event_id: event.id,
        action: action,
        created_at: new Date().toISOString()
      },
      { onConflict: 'user_id,event_id' }
    );

    if (error) {
      console.error('Supabase write error:', error);
    }
  }
}

/**
 * Fetch full user selection history
 */
export async function getUserHistory() {
  const clientId = getOrCreateClientId();

  if (supabase) {
    const { data, error } = await supabase
      .from('user_interactions')
      .select(`
        id,
        action,
        created_at,
        events (*)
      `)
      .eq('user_id', clientId)
      .order('created_at', { ascending: false });

    if (!error && data && data.length > 0) {
      return data;
    }
  }

  // Fallback to local storage if DB fetch fails or returns empty
  try {
    return JSON.parse(localStorage.getItem('vibe_history') || '[]');
  } catch (err) {
    return [];
  }
}