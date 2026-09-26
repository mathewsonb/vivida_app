import React, { useState } from 'react';
import { hashEmail } from '../lib/crypto';
import UserHistoryReport from './UserHistoryReport';
import { X, ShieldCheck, Lock, Share2, Mail, MessageSquare, Copy, Check, Calendar, MapPin } from 'lucide-react';

export default function HistoryModal({ 
  isOpen, 
  onClose, 
  userHash, 
  onUserHashCreated, 
  bookmarks = [] 
}) {
  const [emailInput, setEmailInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [selectedEventIds, setSelectedEventIds] = useState([]);
  const [copied, setCopied] = useState(false);

  if (!isOpen) return null;

  const handleOptIn = async (e) => {
    e.preventDefault();
    if (!emailInput) return;

    setLoading(true);
    try {
      const hash = await hashEmail(emailInput);
      localStorage.setItem('vivida_user_hash', hash);
      onUserHashCreated(hash);
      setEmailInput('');
    } catch (err) {
      console.error('Error hashing email:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleClearSession = () => {
    localStorage.removeItem('vivida_user_hash');
    onUserHashCreated(null);
  };

  const toggleSelectEvent = (id) => {
    setSelectedEventIds((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]
    );
  };

  const toggleSelectAll = () => {
    if (selectedEventIds.length === bookmarks.length) {
      setSelectedEventIds([]);
    } else {
      setSelectedEventIds(bookmarks.map((b) => b.id));
    }
  };

  // Format selection for sharing
  const selectedEvents = bookmarks.filter((e) => selectedEventIds.includes(e.id));
  
  const generateShareText = () => {
    if (selectedEvents.length === 0) return '';
    const lines = selectedEvents.map(
      (e) => `• ${e.title || 'Event'} (${e.venue_name || e.location || 'Local Venue'})`
    );
    return `Hey! Check out these events I picked out on Vivida:\n\n${lines.join('\n')}\n\nFind your vibe at ${window.location.origin}`;
  };

  const handleCopyShare = async () => {
    const text = generateShareText();
    if (!text) return;
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleEmailShare = () => {
    const text = generateShareText();
    if (!text) return;
    const subject = encodeURIComponent('Events to check out!');
    const body = encodeURIComponent(text);
    window.location.href = `mailto:?subject=${subject}&body=${body}`;
  };

  const handleSMSShare = () => {
    const text = generateShareText();
    if (!text) return;
    const body = encodeURIComponent(text);
    window.location.href = `sms:?&body=${body}`;
  };

  return (
    <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center p-4">
      <div className="bg-parchment-100 rounded-3xl p-6 max-w-lg w-full border border-parchment-300 shadow-2xl relative max-h-[90vh] flex flex-col">
        
        {/* Close Button */}
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-parchment-700 hover:text-parchment-900 p-1 z-10"
          aria-label="Close modal"
        >
          <X className="w-5 h-5" />
        </button>

        {!userHash ? (
          /* Unauthenticated State */
          <div className="py-2 overflow-y-auto pr-1">
            <div className="w-12 h-12 bg-terracotta/10 rounded-2xl flex items-center justify-center mb-4 text-terracotta">
              <Lock className="w-6 h-6" />
            </div>
            <h3 className="font-serif text-xl font-bold text-parchment-900 mb-2">
              Sync History & Vibe Profile
            </h3>
            <p className="text-sm text-parchment-700 mb-6 leading-relaxed">
              Enter your email to view your saved events and mood analytics. Your email address is immediately converted into a zero-PII cryptographic hash on your device—we never store or see your raw email.
            </p>

            <form onSubmit={handleOptIn} className="space-y-3">
              <input
                type="email"
                placeholder="you@example.com"
                value={emailInput}
                onChange={(e) => setEmailInput(e.target.value)}
                className="w-full px-4 py-2.5 rounded-xl border border-parchment-300 bg-white text-parchment-900 focus:outline-none focus:ring-2 focus:ring-terracotta text-sm"
                required
              />
              <button
                type="submit"
                disabled={loading}
                className="w-full py-2.5 bg-terracotta text-white rounded-xl font-medium text-sm hover:bg-terracotta-hover transition-colors shadow-sm disabled:opacity-50"
              >
                {loading ? 'Generating Secure Hash...' : 'Access My History'}
              </button>
            </form>

            <div className="flex items-center gap-2 mt-4 text-xs text-parchment-700 justify-center">
              <ShieldCheck className="w-4 h-4 text-emerald-600" />
              <span>Zero-PII guarantee • Client-side SHA-256</span>
            </div>
          </div>
        ) : (
          /* Authenticated State & Event Selection Deck */
          <div className="flex flex-col h-full overflow-hidden">
            <div className="mb-4 pr-6">
              <h3 className="font-serif text-xl font-bold text-parchment-900">
                Your Saved Vibe History
              </h3>
              <p className="text-xs text-parchment-700">
                Select events below to generate invite links or send via Email/Text.
              </p>
            </div>

            {/* Saved Bookmarks List */}
            <div className="flex-1 overflow-y-auto space-y-2.5 pr-1 my-2">
              {bookmarks.length === 0 ? (
                <div className="text-center py-8 text-parchment-600 text-sm">
                  No saved events in this session yet. Swipe right or tap "Totally My Vibe" on cards to bookmark!
                </div>
              ) : (
                <>
                  <div className="flex items-center justify-between text-xs text-parchment-700 pb-1">
                    <button
                      onClick={toggleSelectAll}
                      className="font-semibold text-terracotta hover:underline"
                    >
                      {selectedEventIds.length === bookmarks.length ? 'Deselect All' : 'Select All'}
                    </button>
                    <span>{selectedEventIds.length} selected</span>
                  </div>

                  {bookmarks.map((event) => {
                    const isSelected = selectedEventIds.includes(event.id);
                    return (
                      <div
                        key={event.id}
                        onClick={() => toggleSelectEvent(event.id)}
                        className={`p-3 rounded-2xl border transition-all cursor-pointer flex items-start gap-3 ${
                          isSelected
                            ? 'bg-terracotta/5 border-terracotta'
                            : 'bg-white border-parchment-200 hover:border-parchment-300'
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => {}} // Handled by parent div onClick
                          className="mt-1 h-4 w-4 accent-terracotta rounded cursor-pointer"
                        />
                        <div className="flex-1 min-w-0">
                          <h4 className="text-sm font-semibold text-parchment-900 truncate">
                            {event.title}
                          </h4>
                          <div className="flex items-center gap-3 text-xs text-parchment-600 mt-1">
                            {(event.venue_name || event.location) && (
                              <span className="flex items-center gap-1 truncate">
                                <MapPin className="w-3 h-3 text-parchment-400 shrink-0" />
                                {event.venue_name || event.location}
                              </span>
                            )}
                            {event.start_time && (
                              <span className="flex items-center gap-1 shrink-0">
                                <Calendar className="w-3 h-3 text-parchment-400 shrink-0" />
                                {new Date(event.start_time).toLocaleDateString()}
                              </span>
                            )}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </>
              )}
            </div>

            {/* Sharing Toolbar */}
            {selectedEventIds.length > 0 && (
              <div className="pt-3 border-t border-parchment-200 flex items-center justify-between gap-2">
                <button
                  onClick={handleCopyShare}
                  className="flex-1 flex items-center justify-center gap-1.5 py-2 px-3 bg-white border border-parchment-300 hover:bg-parchment-50 text-parchment-800 rounded-xl text-xs font-semibold transition-colors"
                >
                  {copied ? <Check className="w-3.5 h-3.5 text-emerald-600" /> : <Copy className="w-3.5 h-3.5" />}
                  {copied ? 'Copied' : 'Copy'}
                </button>
                <button
                  onClick={handleSMSShare}
                  className="flex-1 flex items-center justify-center gap-1.5 py-2 px-3 bg-white border border-parchment-300 hover:bg-parchment-50 text-parchment-800 rounded-xl text-xs font-semibold transition-colors"
                >
                  <MessageSquare className="w-3.5 h-3.5 text-indigo-600" />
                  SMS
                </button>
                <button
                  onClick={handleEmailShare}
                  className="flex-1 flex items-center justify-center gap-1.5 py-2 px-3 bg-terracotta hover:bg-terracotta-hover text-white rounded-xl text-xs font-semibold transition-colors shadow-sm"
                >
                  <Mail className="w-3.5 h-3.5" />
                  Invite
                </button>
              </div>
            )}

            {/* Embedded History Report Component */}
            <div className="mt-4 pt-3 border-t border-parchment-200">
              <UserHistoryReport userHash={userHash} onClearSession={handleClearSession} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}