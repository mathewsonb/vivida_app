import { supabase } from './supabaseClient';
import { getOrCreateClientId } from '../utils/session';

/**
 * Non-blocking interaction tracking to Supabase.
 */
export async function trackInteraction({ eventId, action, moodVector, userHash = null }) {
  const clientId = getOrCreateClientId();
  if (!clientId) {
    console.warn('Skipping tracking: Client ID unavailable');
    return;
  }

  const payload = {
    client_id: clientId,
    event_id: eventId,
    action: action, // 'vibe', 'not_vibe', 'maybe'
    mood_energy: moodVector?.energy ?? null,
    mood_social: moodVector?.social ?? null,
    mood_novelty: moodVector?.novelty ?? null,
    user_hash: userHash,
    created_at: new Date().toISOString()
  };

  try {
    const { error } = await supabase.from('interactions').insert([payload]);
    if (error) {
      console.error('Supabase interaction tracking error:', error.message);
    }
  } catch (err) {
    console.error('Failed to log interaction:', err);
  }
}