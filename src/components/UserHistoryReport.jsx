import React, { useState, useEffect } from 'react';
import { supabase } from '../lib/supabase';
import { Sparkles, Calendar, Heart, Bookmark, Activity } from 'lucide-react';

export default function UserHistoryReport({ userHash }) {
  const [loading, setLoading] = useState(true);
  const [summary, setSummary] = useState(null);
  const [history, setHistory] = useState([]);

  useEffect(() => {
    if (!userHash) {
      setLoading(false);
      return;
    }

    async function fetchUserData() {
      setLoading(true);
      try {
        // 1. Fetch aggregate mood summary view
        const { data: summaryData } = await supabase
          .from('user_interest_summaries')
          .select('*')
          .eq('user_hash', userHash)
          .single();

        if (summaryData) setSummary(summaryData);

        // 2. Fetch detailed interaction history
        const { data: historyData } = await supabase
          .from('event_conversions')
          .select(`
            id,
            interaction_type,
            created_at,
            events ( id, title, category, energy, social, novelty, vendor_url )
          `)
          .eq('user_hash', userHash)
          .order('created_at', { ascending: false });

        if (historyData) setHistory(historyData);
      } catch (err) {
        console.error('Error fetching user report:', err);
      } finally {
        setLoading(false);
      }
    }

    fetchUserData();
  }, [userHash]);

  if (!userHash) {
    return (
      <div className="bg-parchment-200/60 rounded-2xl p-6 text-center border border-parchment-300">
        <p className="text-parchment-800 font-medium mb-1">No Active History Session</p>
        <p className="text-sm text-parchment-700">
          Sync your email above to save your event history and generate your personalized mood profile.
        </p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="animate-pulse space-y-4 p-4">
        <div className="h-24 bg-parchment-300 rounded-xl"></div>
        <div className="h-40 bg-parchment-300 rounded-xl"></div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Mood Profile Card */}
      <div className="bg-parchment-100 rounded-2xl p-5 border border-parchment-300 shadow-sm">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Sparkles className="w-5 h-5 text-terracotta" />
            <h3 className="font-serif font-bold text-lg text-parchment-900">
              Your Vibe Profile
            </h3>
          </div>
          <span className="text-xs bg-parchment-200 text-parchment-800 px-2.5 py-1 rounded-full font-medium">
            {summary?.total_interactions || 0} Saved Events
          </span>
        </div>

        {summary ? (
          <div className="space-y-3">
            {/* Energy Axis */}
            <div>
              <div className="flex justify-between text-xs font-medium mb-1">
                <span className="text-parchment-800">Energy Level</span>
                <span className="text-terracotta">{Math.round((summary.avg_energy_pref || 0.5) * 100)}%</span>
              </div>
              <div className="w-full h-2 bg-parchment-300 rounded-full overflow-hidden">
                <div 
                  className="h-full bg-terracotta transition-all duration-500"
                  style={{ width: `${(summary.avg_energy_pref || 0.5) * 100}%` }}
                />
              </div>
            </div>

            {/* Social Axis */}
            <div>
              <div className="flex justify-between text-xs font-medium mb-1">
                <span className="text-parchment-800">Social Density</span>
                <span className="text-terracotta">{Math.round((summary.avg_social_pref || 0.5) * 100)}%</span>
              </div>
              <div className="w-full h-2 bg-parchment-300 rounded-full overflow-hidden">
                <div 
                  className="h-full bg-terracotta transition-all duration-500"
                  style={{ width: `${(summary.avg_social_pref || 0.5) * 100}%` }}
                />
              </div>
            </div>

            {/* Novelty Axis */}
            <div>
              <div className="flex justify-between text-xs font-medium mb-1">
                <span className="text-parchment-800">Novelty & Curiosity</span>
                <span className="text-terracotta">{Math.round((summary.avg_novelty_pref || 0.5) * 100)}%</span>
              </div>
              <div className="w-full h-2 bg-parchment-300 rounded-full overflow-hidden">
                <div 
                  className="h-full bg-terracotta transition-all duration-500"
                  style={{ width: `${(summary.avg_novelty_pref || 0.5) * 100}%` }}
                />
              </div>
            </div>
          </div>
        ) : (
          <p className="text-xs text-parchment-700 italic">
            Interact with a few events to generate your mood preference metrics!
          </p>
        )}
      </div>

      {/* Activity History List */}
      <div>
        <h4 className="font-serif font-bold text-parchment-900 mb-3 flex items-center gap-2 text-base">
          <Activity className="w-4 h-4 text-terracotta" />
          Saved Event History
        </h4>

        {history.length === 0 ? (
          <p className="text-sm text-parchment-700 italic">No activity recorded yet.</p>
        ) : (
          <div className="space-y-2.5">
            {history.map((item) => (
              <div 
                key={item.id} 
                className="flex items-center justify-between p-3 bg-white rounded-xl border border-parchment-300 shadow-sm hover:border-terracotta/40 transition-colors"
              >
                <div className="flex items-center gap-3">
                  {item.interaction_type === 'vibe' ? (
                    <Heart className="w-4 h-4 text-terracotta fill-terracotta" />
                  ) : (
                    <Bookmark className="w-4 h-4 text-parchment-700" />
                  )}
                  <div>
                    <a 
                      href={item.events?.vendor_url || '#'} 
                      target="_blank" 
                      rel="noopener noreferrer"
                      className="font-medium text-sm text-parchment-900 hover:text-terracotta transition-colors line-clamp-1"
                    >
                      {item.events?.title || 'Local Event'}
                    </a>
                    <span className="text-xs text-parchment-700 capitalize">
                      {item.events?.category} • {new Date(item.created_at).toLocaleDateString()}
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}