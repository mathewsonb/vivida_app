// src/components/HistoryPage.jsx

import React, { useState, useEffect } from 'react';
import { 
  Sparkles, 
  MapPin, 
  Calendar, 
  Tag, 
  ExternalLink, 
  Share2, 
  CheckSquare, 
  Square, 
  ArrowLeft, 
  Flame, 
  Bookmark, 
  XCircle,
  Copy,
  Check
} from 'lucide-react';
import { getUserHistory } from '../services/historyService';

const FALLBACK_IMAGE = "https://images.unsplash.com/photo-1492684223066-81342ee5ff30?auto=format&fit=crop&w=800&q=80";

export default function HistoryPage({ onBack }) {
  const [historyItems, setHistoryItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('totally_vibe'); // 'totally_vibe', 'maybe_later', 'not_my_scene'
  const [selectedIds, setSelectedIds] = useState([]);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    async function loadData() {
      setLoading(true);
      const items = await getUserHistory();
      setHistoryItems(items);
      setLoading(false);
    }
    loadData();
  }, []);

  const filteredItems = historyItems.filter(item => item.action === activeTab);

  // Toggle selection for batch actions
  const toggleSelect = (id) => {
    setSelectedIds(prev => 
      prev.includes(id) ? prev.filter(i => i !== id) : [...prev, id]
    );
  };

  const selectAll = () => {
    if (selectedIds.length === filteredItems.length) {
      setSelectedIds([]);
    } else {
      setSelectedIds(filteredItems.map(item => item.id));
    }
  };

  // Helper to parse dates safely
  const formatDates = (rawDateStr) => {
    if (!rawDateStr) return 'Register';
    const dateOptions = { month: 'short', day: 'numeric', year: 'numeric' };
    const strVal = String(rawDateStr);

    const parseLocal = (dStr) => {
      const trimmed = dStr.trim();
      if (/^\d{4}-\d{2}-\d{2}$/.test(trimmed)) {
        return new Date(`${trimmed}T00:00:00`);
      }
      return new Date(trimmed);
    };

    if (strVal.includes(',')) {
      const dates = strVal.split(',').map(d => parseLocal(d)).filter(d => !isNaN(d.getTime()));
      if (dates.length >= 2) {
        return `${dates[0].toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} - ${dates[dates.length - 1].toLocaleDateString('en-US', dateOptions)}`;
      } else if (dates.length === 1) {
        return dates[0].toLocaleDateString('en-US', dateOptions);
      }
    }

    const parsedDate = parseLocal(strVal);
    return !isNaN(parsedDate.getTime()) ? parsedDate.toLocaleDateString('en-US', dateOptions) : strVal;
  };

  // Extract array of schedules
  const getScheduleList = (event) => {
    let raw = event?.schedule;
    if (!raw) return [];
    if (typeof raw === 'string') {
      try {
        raw = JSON.parse(raw);
        if (typeof raw === 'string') raw = JSON.parse(raw);
      } catch (err) {
        return [];
      }
    }
    if (Array.isArray(raw)) {
      return raw.map(i => ({
        label: formatDates(i.start_time || i.date || i.time),
        url: i.register || i.url || i.external_url || event.external_url || '#'
      }));
    }
    if (typeof raw === 'object' && raw !== null) {
      if ('start_time' in raw || 'register' in raw) {
        return [{ label: formatDates(raw.start_time), url: raw.register || event.external_url || '#' }];
      }
      return Object.entries(raw).map(([key, value]) => ({
        label: formatDates(key),
        url: typeof value === 'string' ? value : event.external_url || '#'
      }));
    }
    return [];
  };

  // Generate share payload string
  const formatShareText = (itemsToShare) => {
    let text = "🎉 Check out these local event vibes I found!\n\n";
    itemsToShare.forEach((item, index) => {
      const ev = item.events || {};
      const venue = ev.venue_name || ev.venue || 'Local Venue';
      const schedules = getScheduleList(ev);
      const mainLink = schedules[0]?.url || ev.external_url || '';

      text += `${index + 1}. ${ev.title || 'Event'}\n`;
      text += `📍 ${venue}${ev.address ? ` (${ev.address})` : ''}\n`;
      if (mainLink) text += `🔗 ${mainLink}\n`;
      text += `\n`;
    });
    return text.trim();
  };

  // Share handler (Web Share API or Clipboard Fallback)
  const handleShare = async (specificItem = null) => {
    const itemsToShare = specificItem 
      ? [specificItem] 
      : historyItems.filter(item => selectedIds.includes(item.id));

    if (itemsToShare.length === 0) return;

    const shareText = formatShareText(itemsToShare);

    if (navigator.share) {
      try {
        await navigator.share({
          title: 'Local Vibes Selections',
          text: shareText
        });
      } catch (err) {
        if (err.name !== 'AbortError') console.error('Error sharing:', err);
      }
    } else {
      // Clipboard fallback
      try {
        await navigator.clipboard.writeText(shareText);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      } catch (err) {
        console.error('Clipboard write failed:', err);
      }
    }
  };

  return (
    <div className="min-h-screen bg-parchment-50 text-parchment-900 pb-12 px-4 sm:px-6">
      <div className="max-w-3xl mx-auto pt-6 space-y-6">
        
        {/* HEADER BAR */}
        <div className="flex items-center justify-between border-b border-parchment-200 pb-4">
          <button 
            onClick={onBack}
            className="flex items-center gap-2 text-sm font-semibold text-parchment-700 hover:text-terracotta transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            <span>Back to Deck</span>
          </button>
          <h1 className="font-serif text-xl font-bold text-parchment-900">Your Selection History</h1>
        </div>

        {/* TAB FILTER CONTROL */}
        <div className="flex items-center justify-between gap-2 bg-parchment-100 p-1.5 rounded-2xl border border-parchment-200">
          <button
            onClick={() => { setActiveTab('totally_vibe'); setSelectedIds([]); }}
            className={`flex-1 flex items-center justify-center gap-2 py-2 rounded-xl text-xs font-bold transition-all ${
              activeTab === 'totally_vibe' 
                ? 'bg-emerald-600 text-white shadow-md' 
                : 'text-parchment-700 hover:bg-parchment-200/60'
            }`}
          >
            <Flame className="w-3.5 h-3.5" />
            <span>Vibes</span>
          </button>

          <button
            onClick={() => { setActiveTab('maybe_later'); setSelectedIds([]); }}
            className={`flex-1 flex items-center justify-center gap-2 py-2 rounded-xl text-xs font-bold transition-all ${
              activeTab === 'maybe_later' 
                ? 'bg-amber-600 text-white shadow-md' 
                : 'text-parchment-700 hover:bg-parchment-200/60'
            }`}
          >
            <Bookmark className="w-3.5 h-3.5 fill-current" />
            <span>Saved</span>
          </button>

          <button
            onClick={() => { setActiveTab('not_my_scene'); setSelectedIds([]); }}
            className={`flex-1 flex items-center justify-center gap-2 py-2 rounded-xl text-xs font-bold transition-all ${
              activeTab === 'not_my_scene' 
                ? 'bg-rose-600 text-white shadow-md' 
                : 'text-parchment-700 hover:bg-parchment-200/60'
            }`}
          >
            <XCircle className="w-3.5 h-3.5" />
            <span>Passed</span>
          </button>
        </div>

        {/* BATCH ACTION CONTROLS */}
        {filteredItems.length > 0 && (
          <div className="flex items-center justify-between bg-white px-4 py-2.5 rounded-2xl border border-parchment-200 shadow-sm text-xs font-medium text-parchment-800">
            <button 
              onClick={selectAll}
              className="flex items-center gap-2 hover:text-terracotta transition-colors"
            >
              {selectedIds.length === filteredItems.length && filteredItems.length > 0 ? (
                <CheckSquare className="w-4 h-4 text-terracotta" />
              ) : (
                <Square className="w-4 h-4 text-parchment-400" />
              )}
              <span>Select All ({filteredItems.length})</span>
            </button>

            {selectedIds.length > 0 && (
              <button
                onClick={() => handleShare()}
                className="flex items-center gap-1.5 bg-terracotta text-white px-3 py-1.5 rounded-xl font-bold hover:bg-terracotta/90 transition-all shadow-sm"
              >
                {copied ? <Check className="w-3.5 h-3.5" /> : <Share2 className="w-3.5 h-3.5" />}
                <span>{copied ? 'Copied Details!' : `Share Selected (${selectedIds.length})`}</span>
              </button>
            )}
          </div>
        )}

        {/* CARDS LIST */}
        {loading ? (
          <div className="py-20 text-center text-xs text-parchment-600 animate-pulse">
            Loading your history...
          </div>
        ) : filteredItems.length === 0 ? (
          <div className="py-16 bg-white rounded-3xl border border-dashed border-parchment-200 text-center p-6 space-y-2">
            <Sparkles className="w-8 h-8 text-terracotta/40 mx-auto" />
            <h3 className="font-serif font-bold text-parchment-900">No events saved under this vibe yet</h3>
            <p className="text-xs text-parchment-700 max-w-xs mx-auto">
              Head back to the deck to start exploring and picking your local vibes.
            </p>
          </div>
        ) : (
          <div className="space-y-4">
            {filteredItems.map(item => {
              const event = item.events || {};
              const schedules = getScheduleList(event);
              const venue = event.venue_name || event.venue || 'Local Venue';
              const isSelected = selectedIds.includes(item.id);

              const mapUrl = event.latitude && event.longitude 
                ? `https://www.google.com/maps/search/?api=1&query=${event.latitude},${event.longitude}`
                : `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(venue + ' ' + (event.address || 'Tacoma, WA'))}`;

              return (
                <div 
                  key={item.id}
                  className={`relative bg-white rounded-3xl border transition-all shadow-md overflow-hidden flex flex-col md:flex-row ${
                    isSelected ? 'border-terracotta ring-1 ring-terracotta' : 'border-parchment-200'
                  }`}
                >
                  {/* SELECTION CHECKBOX */}
                  <button
                    onClick={() => toggleSelect(item.id)}
                    className="absolute top-3 left-3 z-20 bg-white/90 backdrop-blur-md p-1.5 rounded-xl border border-parchment-200 shadow-sm"
                  >
                    {isSelected ? (
                      <CheckSquare className="w-4 h-4 text-terracotta" />
                    ) : (
                      <Square className="w-4 h-4 text-parchment-400" />
                    )}
                  </button>

                  {/* IMAGE SIDE / HEADER */}
                  <div className="relative h-48 md:h-auto md:w-56 shrink-0 bg-parchment-100 overflow-hidden">
                    <img 
                      src={event.image_url || FALLBACK_IMAGE} 
                      alt={event.title || 'Event'} 
                      className="w-full h-full object-cover"
                      onError={(e) => { e.target.onerror = null; e.target.src = FALLBACK_IMAGE; }}
                    />
                    <div className="absolute inset-0 bg-gradient-to-t from-black/50 via-transparent to-transparent md:hidden" />
                    
                    <span className="absolute bottom-3 left-3 text-[10px] font-bold uppercase tracking-wider bg-black/60 text-white px-2 py-0.5 rounded-full border border-white/20 backdrop-blur-md">
                      {event.category || 'Experience'}
                    </span>
                  </div>

                  {/* DETAILS CONTENT */}
                  <div className="p-5 flex-1 flex flex-col justify-between space-y-4">
                    <div className="space-y-2">
                      <div className="flex items-start justify-between gap-3">
                        <h3 className="font-serif font-bold text-lg text-parchment-900 leading-tight">
                          {event.title}
                        </h3>

                        {/* SINGLE ITEM SHARE BUTTON */}
                        <button
                          onClick={() => handleShare(item)}
                          className="shrink-0 p-2 text-parchment-600 hover:text-terracotta hover:bg-parchment-100 rounded-xl transition-colors"
                          title="Share event details"
                        >
                          <Share2 className="w-4 h-4" />
                        </button>
                      </div>

                      <p className="text-xs text-parchment-800 leading-relaxed">
                        {event.description}
                      </p>
                    </div>

                    {/* METADATA & SCHEDULE REDIRECTS */}
                    <div className="space-y-3 pt-3 border-t border-parchment-100 text-xs">
                      <div className="flex flex-wrap gap-y-1.5 gap-x-4 text-parchment-800">
                        {mapUrl && (
                          <a 
                            href={mapUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="flex items-center gap-1.5 text-terracotta font-medium hover:underline truncate"
                          >
                            <MapPin className="w-3.5 h-3.5 shrink-0" />
                            <span className="truncate">{venue} {event.address ? `• ${event.address}` : ''}</span>
                          </a>
                        )}

                        {event.price_info && (
                          <div className="flex items-center gap-1.5 text-parchment-700">
                            <Tag className="w-3.5 h-3.5 text-terracotta shrink-0" />
                            <span>{event.price_info}</span>
                          </div>
                        )}
                      </div>

                      {/* DYNAMIC REGISTRATION LINKS */}
                      <div className="space-y-1.5">
                        <div className="flex items-center gap-1 text-[11px] font-medium text-parchment-700">
                          <Calendar className="w-3.5 h-3.5 text-terracotta" />
                          <span>Dates & Registration Links:</span>
                        </div>

                        <div className="flex items-center gap-2 overflow-x-auto pb-1 no-scrollbar">
                          {schedules.length > 0 ? (
                            schedules.map((sched, idx) => (
                              <a
                                key={idx}
                                href={sched.url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="shrink-0 flex items-center gap-1 bg-parchment-100 hover:bg-terracotta hover:text-white text-parchment-900 border border-parchment-200 font-semibold py-1 px-2.5 rounded-xl text-xs transition-all shadow-sm"
                              >
                                <span>{sched.label}</span>
                                <ExternalLink className="w-3 h-3 opacity-70" />
                              </a>
                            ))
                          ) : (
                            <span className="text-xs text-parchment-600 italic">No direct links available</span>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}