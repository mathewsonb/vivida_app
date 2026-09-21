// src/components/EmailPromptModal.jsx
import React, { useState } from 'react';
import { Mail, Sparkles, ShieldCheck, X } from 'lucide-react';

export default function EmailPromptModal({ isOpen, onClose, onSuccess }) {
  const [email, setEmail] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  if (!isOpen) return null;

  const handleHashAndSubmit = async (e) => {
    e.preventDefault();
    if (!email || !email.includes('@')) return;

    setIsSubmitting(true);
    try {
      // 1. Normalize email (lowercase & trimmed)
      const normalized = email.trim().toLowerCase();

      // 2. Hash using browser native Web Crypto SHA-256 API
      const encoder = new TextEncoder();
      const data = encoder.encode(normalized);
      const hashBuffer = await crypto.subtle.digest('SHA-256', data);

      // 3. Convert ArrayBuffer to Hex string
      const hashArray = Array.from(new Uint8Array(hashBuffer));
      const hexHash = hashArray
        .map((b) => b.toString(16).padStart(2, '0'))
        .join('');

      onSuccess(hexHash);
    } catch (err) {
      console.error('Error generating hash:', err);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-xl max-w-sm w-full p-6 relative border border-parchment-200">
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-parchment-400 hover:text-parchment-700 transition-colors"
          title="Skip for now"
        >
          <X className="w-5 h-5" />
        </button>

        <div className="flex items-center gap-2 mb-3 text-terracotta">
          <Sparkles className="w-5 h-5" />
          <h2 className="text-lg font-bold text-parchment-900">
            Save Your Vibe History
          </h2>
        </div>

        <p className="text-xs text-parchment-600 mb-4 leading-relaxed">
          Enter your email to sync your saved events across visits. Your email is hashed client-side—we never see or store your raw address.
        </p>

        <form onSubmit={handleHashAndSubmit} className="space-y-3">
          <div>
            <label className="block text-xs font-semibold text-parchment-700 mb-1">
              Email Address
            </label>
            <div className="relative">
              <Mail className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-parchment-400" />
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                className="w-full pl-9 pr-3 py-2 border border-parchment-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-terracotta text-parchment-900 bg-parchment-50"
              />
            </div>
          </div>

          <div className="flex items-center gap-1.5 text-[11px] text-emerald-700 font-medium">
            <ShieldCheck className="w-3.5 h-3.5" />
            <span>Zero-PII SHA-256 Hashing Guarantee</span>
          </div>

          <div className="flex items-center justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-3 py-1.5 text-xs font-medium text-parchment-600 hover:text-parchment-900 transition-colors"
            >
              Skip for now
            </button>
            <button
              type="submit"
              disabled={isSubmitting}
              className="px-4 py-2 bg-terracotta hover:bg-terracotta-hover text-white rounded-lg text-xs font-semibold transition-colors shadow-sm disabled:opacity-50"
            >
              {isSubmitting ? 'Hashing...' : 'Sync History'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}