// src/lib/tracking.js
import { supabase } from './supabase';

/**
 * Logs an event interaction into Supabase with optional zero-PII user hash
 */
export async function trackEventInteraction(eventId, interactionType = 'vibe') {
  const userHash = localStorage.getItem('vivida_user_hash') || null;

  try {
    await supabase.from('event_conversions').insert({
      event_id: eventId,
      interaction_type: interactionType, // 'vibe', 'save', or 'vendor_click'
      user_hash: userHash
    });
  } catch (err) {
    console.error('Failed to log event conversion:', err);
  }
}