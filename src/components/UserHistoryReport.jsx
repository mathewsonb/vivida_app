import React, { useState, useEffect } from 'react';
import { supabase } from '../lib/supabase';
import { Sparkles, Heart, Bookmark, Activity, MailCheck, Send, Loader2 } from 'lucide-react';

export default function UserHistoryReport({ userHash, onClearSession }) {
  const [loading, setLoading] = useState(true);
  const [summary, setSummary] = useState(null);
  const [history, setHistory] = useState([]);
  
  // Email Export States
  const [exportEmail, setExportEmail] = useState('');
  const [sendingExport, setSendingExport] = useState(false);
  const [exportSent, setExportSent] = useState(false);

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

  const handleExportHistory = async (e) => {
    e.preventDefault();
    if (!exportEmail || history.length === 0) return;

    setSendingExport(true);
    try {
      // Call Supabase Edge Function to deliver email digest securely
      const { error } = await supabase.functions.invoke('send-history-digest', {
        body: { 
          recipientEmail: exportEmail, 
          events: history.map(h => ({
            title: h.events?.title,
            category: h.events?.category,
            url: h.events?.vendor_url
          }))
        }
      });

      if (!error) {
        setExportSent(true);
        setExportEmail('');
      }
    } catch (err) {
      console.error('Failed to send history export:', err);
    } finally {
      setSendingExport(false);
    }
  };

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
      {/* Vibe Profile Summary */}
      <div className="bg-parchment-100 rounded-2xl p-5 border border-parchment-300 shadow-sm">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Sparkles className="w-5 h-5 text-terracotta" />
            <h3 className="font-serif font-bold text-lg text-parchment-900">Your Vibe Profile</h3>
          </div>
          <span className="text-xs bg-parchment-200 text-parchment-800 px-2.5 py-1 rounded-full font-medium">
            {summary?.total_interactions || history.length} Interactions
          </span>
        </div>

        {summary ? (
          <div className="space-y-3">
            <div>
              <div className="flex justify-between text-xs font-medium mb-1">
                <span className="text-parchment-800">Energy Level</span>
                <span className="text-terracotta">{Math.round((summary.avg_energy_pref || 0.5) * 100)}%</span>
              </div>
              <div className="w-full h-2 bg-parchment-300 rounded-full overflow-hidden">
                <div className="h-full bg-terracotta transition-all duration-500" style={{ width: `${(summary.avg_energy_pref || 0.5) * 100}%` }} />
              </div>
            </div>

            <div>
              <div className="flex justify-between text-xs font-medium mb-1">
                <span className="text-parchment-800">Social Density</span>
                <span className="text-terracotta">{Math.round((summary.avg_social_pref || 0.5) * 100)}%</span>
              </div>
              <div className="w-full h-2 bg-parchment-300 rounded-full overflow-hidden">
                <div className="h-full bg-terracotta transition-all duration-500" style={{ width: `${(summary.avg_social_pref || 0.5) * 100}%` }} />
              </div>
            </div>

            <div>
              <div className="flex justify-between text-xs font-medium mb-1">
                <span className="text-parchment-800">Novelty & Curiosity</span>
                <span className="text-terracotta">{Math.round((summary.avg_novelty_pref || 0.5) * 100)}%</span>
              </div>
              <div className="w-full h-2 bg-parchment-300 rounded-full overflow-hidden">
                <div className="h-full bg-terracotta transition-all duration-500" style={{ width: `${(summary.avg_novelty_pref || 0.5) * 100}%` }} />
              </div>
            </div>
          </div>
        ) : (
          <p className="text-xs text-parchment-700 italic">Interact with more events to populate your mood metrics!</p>
        )}
      </div>

      {/* History List */}
      <div>
        <h4 className="font-serif font-bold text-parchment-900 mb-3 flex items-center gap-2 text-base">
          <Activity className="w-4 h-4 text-terracotta" />
          Saved Event History
        </h4>

        {history.length === 0 ? (
          <p className="text-sm text-parchment-700 italic">No activity recorded yet.</p>
        ) : (
          <div className="space-y-2.5 max-h-60 overflow-y-auto pr-1">
            {history.map((item) => (
              <div key={item.id} className="flex items-center justify-between p-3 bg-white rounded-xl border border-parchment-300 shadow-sm">
                <div className="flex items-center gap-3">
                  {item.interaction_type === 'vibe' ? (
                    <Heart className="w-4 h-4 text-terracotta fill-terracotta" />
                  ) : (
                    <Bookmark className="w-4 h-4 text-parchment-700" />
                  )}
                  <div>
                    <a href={item.events?.vendor_url || '#'} target="_blank" rel="noopener noreferrer" className="font-medium text-sm text-parchment-900 hover:text-terracotta line-clamp-1">
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

      {/* Email Export Section */}
      <div className="bg-parchment-200/70 rounded-2xl p-4 border border-parchment-300">
        <h5 className="font-serif font-bold text-sm text-parchment-900 mb-1">Send History to Email</h5>
        <p className="text-xs text-parchment-700 mb-3">
          Export your saved events directly to your inbox without storing your email on our servers.
        </p>

        {exportSent ? (
          <div className="flex items-center gap-2 text-xs text-emerald-700 font-medium bg-emerald-50 p-2.5 rounded-xl border border-emerald-200">
            <MailCheck className="w-4 h-4 text-emerald-600" />
            Digest sent! Check your inbox shortly.
          </div>
        ) : (
          <form onSubmit={handleExportHistory} className="flex gap-2">
            <input
              type="email"
              placeholder="you@example.com"
              value={exportEmail}
              onChange={(e) => setExportEmail(e.target.value)}
              className="flex-1 px-3 py-1.5 rounded-lg border border-parchment-300 bg-white text-xs text-parchment-900 focus:outline-none focus:ring-2 focus:ring-terracotta"
              required
            />
            <button
              type="submit"
              disabled={sendingExport || history.length === 0}
              className="px-3 py-1.5 bg-terracotta text-white text-xs font-medium rounded-lg hover:bg-terracotta-600 transition-colors flex items-center gap-1.5 disabled:opacity-50"
            >
              {sendingExport ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
              Export
            </button>
          </form>
        )}
      </div>

      {/* Session Management */}
      <div className="pt-2 text-center">
        <button
          onClick={onClearSession}
          className="text-xs text-parchment-700 hover:text-terracotta underline"
        >
          Disconnect history session on this device
        </button>
      </div>
    </div>
  );
}