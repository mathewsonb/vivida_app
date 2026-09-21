import React, { useState } from 'react';
import { hashEmail } from '../lib/crypto';
import UserHistoryReport from './UserHistoryReport';
import { X, ShieldCheck, Lock } from 'lucide-react';

export default function HistoryModal({ isOpen, onClose, userHash, onUserHashCreated }) {
  const [emailInput, setEmailInput] = useState('');
  const [loading, setLoading] = useState(false);

  if (!isOpen) return null;

  const handleOptIn = async (e) => {
    e.preventDefault();
    if (!emailInput) return;

    setLoading(true);
    const hash = await hashEmail(emailInput);
    localStorage.setItem('vivida_user_hash', hash);
    onUserHashCreated(hash);
    setEmailInput('');
    setLoading(false);
  };

  const handleClearSession = () => {
    localStorage.removeItem('vivida_user_hash');
    onUserHashCreated(null);
  };

  return (
    <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center p-4">
      <div className="bg-parchment-100 rounded-3xl p-6 max-w-md w-full border border-parchment-300 shadow-2xl relative max-h-[90vh] overflow-y-auto">
        
        {/* Close Button */}
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-parchment-700 hover:text-parchment-900 p-1"
        >
          <X className="w-5 h-5" />
        </button>

        {!userHash ? (
          /* Prompt state if user hash is missing */
          <div className="py-4">
            <div className="w-12 h-12 bg-terracotta/10 rounded-2xl flex items-center justify-center mb-4 text-terracotta">
              <Lock className="w-6 h-6" />
            </div>
            <h3 className="font-serif text-xl font-bold text-parchment-900 mb-2">
              Sync History & Vibe Profile
            </h3>
            <p className="text-sm text-parchment-700 mb-6 leading-relaxed">
              Enter your email to view your saved events and mood analytics. Your email address is immediately converted into a zero-PII cryptographic hash on your device—we never store or see your raw email address.
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
                className="w-full py-2.5 bg-terracotta text-white rounded-xl font-medium text-sm hover:bg-terracotta-600 transition-colors shadow-sm"
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
          /* Render History Report when userHash exists */
          <UserHistoryReport userHash={userHash} onClearSession={handleClearSession} />
        )}
      </div>
    </div>
  );
}