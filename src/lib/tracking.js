// src/lib/tracking.js
import { supabase } from './supabaseClient';

/**
 * Tracks user interactions with events in Supabase.
 * @param {string} userHash - SHA-256 hash of the user's email/identifier
 * @param {string|number} eventId - ID of the event interacted with
 * @param {'not_my_scene' | 'maybe_later' | 'totally_vibe'} interactionType - Interaction outcome
 */
export async function trackEventInteraction(userHash, eventId, interactionType) {
  if (!userHash) return;

  try {
    const { error } = await supabase
      .from('event_conversions')
      .insert({
        user_hash: userHash,
        event_id: eventId,
        interaction_type: interactionType,
        created_at: new Date().toISOString()
      });

    if (error) {
      console.error('Error recording interaction:', error.message);
    }
  } catch (err) {
    console.error('Failed to log interaction:', err);
  }
}