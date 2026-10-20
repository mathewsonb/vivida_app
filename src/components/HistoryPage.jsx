// src/components/HistoryPage.jsx

import React, { useState, useEffect, useMemo } from 'react';
import { 
  Sparkles, 
  MapPin, 
  Calendar as CalendarIcon, 
  Tag, 
  ExternalLink, 
  Share2, 
  CheckSquare, 
  Square, 
  ArrowLeft, 
  Flame, 
  Bookmark, 
  Check,
  List,
  ChevronLeft,
  ChevronRight,
  X
} from 'lucide-react';
import { getUserHistory } from '../services/historyService';

const FALLBACK_IMAGE = "https://images.unsplash.com/photo-1492684223066-81342ee5ff30?auto=format&fit=crop&w=800&q=80";

export default function HistoryPage({ onBack }) {
  const [historyItems, setHistoryItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('totally_vibe'); // 'totally_vibe' | 'maybe_later'
  const [displayMode, setDisplayMode] = useState('weekly'); // 'weekly' | 'monthly'
  const [upcomingOnly, setUpcomingOnly] = useState(false);
  const [selectedIds, setSelectedIds] = useState([]);
  const [copied, setCopied] = useState(false);

  // Active event selected for the detail modal
  const [activeModalEvent, setActiveModalEvent] = useState(null);

  // Month/Year state for monthly grid navigator
  const [currentMonthDate, setCurrentMonthDate] = useState(new Date());

  useEffect(() => {
    async function loadData() {
      setLoading(true);
      const items = await getUserHistory();
      setHistoryItems(items || []);
      setLoading(false);
    }
    loadData();
  }, []);

  const todayStr = useMemo(() => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  }, []);

  // Compute counts for tab badges directly from history items
  const tabCounts = useMemo(() => {
    let vibeCount = 0;
    let laterCount = 0;

    historyItems.forEach((item) => {
      if (item.action === 'totally_vibe' || item.action === 'interested') {
        vibeCount++;
      } else if (item.action === 'maybe_later') {
        laterCount++;
      }
    });

    return { vibeCount, laterCount };
  }, [historyItems]);

  const filteredItems = useMemo(() => {
    return historyItems.filter((item) => {
      if (activeTab === 'totally_vibe') {
        return item.action === 'totally_vibe' || item.action === 'interested';
      }
      return item.action === activeTab;
    });
  }, [historyItems, activeTab]);

  // Selection handlers
  const toggleSelect = (id, e) => {
    if (e) e.stopPropagation();
    setSelectedIds((prev) => 
      prev.includes(id) ? prev.filter((i) => i !== id) : [...prev, id]
    );
  };

  const selectAll = () => {
    if (selectedIds.length === filteredItems.length) {
      setSelectedIds([]);
    } else {
      setSelectedIds(filteredItems.map((item) => item.id || item.historyId));
    }
  };

  // Safe schedule extractor with JSONB Snapshot Fallback
  const getScheduleList = (event) => {
    let raw = event?.schedule;
    if (!raw && (event?.date || event?.start_time)) {
      return [{ dateStr: event.date || event.start_time || '', url: event.external_url || '#' }];
    }
    if (!raw) return [];
    if (typeof raw === 'string') {
      try {
        raw = JSON.parse(raw);
        if (typeof raw === 'string') raw = JSON.parse(raw);
      } catch (err) {
        return [{ dateStr: raw, url: event.external_url || '#' }];
      }
    }
    if (Array.isArray(raw)) {
      return raw.map((i) => ({
        dateStr: i.start_time || i.date || i.time || '',
        url: i.register || i.url || i.external_url || event.external_url || '#'
      }));
    }
    if (typeof raw === 'object' && raw !== null) {
      if ('start_time' in raw || 'register' in raw) {
        return [{ dateStr: raw.start_time || '', url: raw.register || event.external_url || '#' }];
      }
      return Object.entries(raw).map(([key, value]) => ({
        dateStr: key,
        url: typeof value === 'string' ? value : event.external_url || '#'
      }));
    }
    return [];
  };

  // Map events to date keys (YYYY-MM-DD) with upcoming filtering and snapshot safety
  const eventsByDate = useMemo(() => {
    const map = {};

    filteredItems.forEach((item) => {
      const ev = item.events || item.event_snapshot || {};
      let schedules = getScheduleList(ev);

      if (schedules.length === 0) {
        const fallbackDate = item.created_at ? item.created_at.split('T')[0] : todayStr;
        schedules = [{ dateStr: fallbackDate, url: ev.external_url || '#' }];
      }

      schedules.forEach((sched) => {
        const rawDate = String(sched.dateStr).trim();
        let dateKey = null;

        // 1. Try ISO YYYY-MM-DD match
        const isoMatches = rawDate.match(/\d{4}-\d{2}-\d{2}/g);
        if (isoMatches && isoMatches.length > 0) {
          dateKey = isoMatches[0];
        } else if (rawDate) {
          // 2. Fallback to standard Date parsing safely avoiding timezone drop-offs
          const parsed = new Date(rawDate);
          if (!isNaN(parsed.getTime())) {
            const y = parsed.getFullYear();
            const m = String(parsed.getMonth() + 1).padStart(2, '0');
            const d = String(parsed.getDate()).padStart(2, '0');
            dateKey = `${y}-${m}-${d}`;
          }
        }

        if (!dateKey) {
          dateKey = todayStr;
        }

        // Apply "Upcoming Only" Filter constraint if active
        if (upcomingOnly && dateKey < todayStr) return;

        if (!map[dateKey]) map[dateKey] = [];
        map[dateKey].push({
          historyId: item.id || item.event_id,
          event: ev,
          url: sched.url,
          dateKey,
          action: item.action
        });
      });
    });

    return map;
  }, [filteredItems, upcomingOnly, todayStr]);

  // Active dates sorted chronologically
  const activeDaysList = useMemo(() => {
    const dates = Object.keys(eventsByDate).sort();

    return dates.map((dateKey) => {
      const [y, m, d] = dateKey.split('-').map(Number);
      const dateObj = new Date(y, m - 1, d);

      return {
        dateObj,
        dateKey,
        isToday: dateKey === todayStr,
        entries: eventsByDate[dateKey] || []
      };
    }).filter((day) => day.entries.length > 0);
  }, [eventsByDate, todayStr]);

  // Monthly calendar grid generator
  const monthlyGrid = useMemo(() => {
    try {
      const year = currentMonthDate.getFullYear();
      const month = currentMonthDate.getMonth();

      const firstDayIndex = new Date(year, month, 1).getDay();
      const daysInMonth = new Date(year, month + 1, 0).getDate();

      const cells = [];
      for (let i = 0; i < firstDayIndex; i++) {
        cells.push({ isPadding: true, id: `pad-pre-${i}` });
      }

      for (let day = 1; day <= daysInMonth; day++) {
        const mStr = String(month + 1).padStart(2, '0');
        const dStr = String(day).padStart(2, '0');
        const dateKey = `${year}-${mStr}-${dStr}`;

        cells.push({
          isPadding: false,
          dayNumber: day,
          dateKey,
          entries: eventsByDate[dateKey] || []
        });
      }

      return cells;
    } catch (err) {
      console.error("Error generating monthly grid:", err);
      return [];
    }
  }, [currentMonthDate, eventsByDate]);

  // Share handler
  const handleShare = async (specificItem = null) => {
    const itemsToShare = specificItem 
      ? [specificItem] 
      : historyItems.filter((item) => selectedIds.includes(item.id || item.event_id));

    if (itemsToShare.length === 0) return;

    let text = "🎉 Check out these local event vibes I found!\n\n";
    itemsToShare.forEach((item, index) => {
      const ev = item.events || item.event_snapshot || {};
      const venue = ev.venue_name || ev.venue || 'Local Venue';
      const schedules = getScheduleList(ev);
      const mainLink = schedules[0]?.url || ev.external_url || '';

      text += `${index + 1}. ${ev.title || 'Event'}\n`;
      text += `📍 ${venue}${ev.address ? ` (${ev.address})` : ''}\n`;
      if (mainLink) text += `🔗 ${mainLink}\n\n`;
    });

    if (navigator.share) {
      try {
        await navigator.share({ title: 'Local Vibes Selections', text: text.trim() });
      } catch (err) {
        if (err.name !== 'AbortError') console.error('Share error:', err);
      }
    } else {
      try {
        await navigator.clipboard.writeText(text.trim());
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      } catch (err) {
        console.error('Clipboard failed:', err);
      }
    }
  };

  return (
    <div className="h-dvh w-full bg-parchment-50 text-parchment-900 flex flex-col overflow-hidden pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)] relative">
      
      {/* FIXED HEADER BAR */}
      <header className="shrink-0 px-4 pt-4 pb-3 border-b border-parchment-200 bg-parchment-50">
        <div className="max-w-4xl mx-auto flex items-center justify-between">
          <button 
            onClick={onBack}
            className="flex items-center gap-2 text-sm font-semibold text-parchment-700 hover:text-terracotta transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            <span>Back to Deck</span>
          </button>
          <h1 className="font-serif text-lg sm:text-xl font-bold text-parchment-900">Your Selection Calendar</h1>
          <div className="w-12 sm:w-20" />
        </div>
      </header>

      {/* MAIN BODY CONTAINER */}
      <main className="flex-1 overflow-y-auto min-h-0 px-4 sm:px-6 py-4">
        <div className="max-w-4xl mx-auto space-y-4 pb-8">
          
          {/* TOP CONTROLS BAR */}
          <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3">
            
            {/* VIBE FILTER TABS */}
            <div className="flex items-center gap-2 bg-parchment-100 p-1 rounded-2xl border border-parchment-200 flex-1">
              <button
                onClick={() => { setActiveTab('totally_vibe'); setSelectedIds([]); }}
                className={`flex-1 flex items-center justify-center gap-2 py-2 rounded-xl text-xs font-bold transition-all ${
                  activeTab === 'totally_vibe' 
                    ? 'bg-emerald-600 text-white shadow-md' 
                    : 'text-parchment-700 hover:bg-parchment-200/60'
                }`}
              >
                <Flame className="w-3.5 h-3.5" />
                <span>For Now ({tabCounts.vibeCount})</span>
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
                <span>For Later ({tabCounts.laterCount})</span>
              </button>
            </div>

            {/* VIEW MODE & UPCOMING TOGGLE CONTROLS */}
            <div className="flex items-center gap-2 self-end sm:self-auto">
              {/* UPCOMING ONLY TOGGLE */}
              <button
                type="button"
                onClick={() => setUpcomingOnly((prev) => !prev)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-bold border transition-all ${
                  upcomingOnly
                    ? 'bg-terracotta text-white border-terracotta shadow-xs'
                    : 'bg-white text-parchment-700 border-parchment-200 hover:bg-parchment-100'
                }`}
              >
                <CalendarIcon className="w-3.5 h-3.5" />
                <span>Upcoming Only</span>
              </button>

              {/* WEEKLY / MONTHLY VIEW SWITCH */}
              <div className="flex items-center bg-white p-1 rounded-xl border border-parchment-200 shadow-xs">
                <button
                  onClick={() => setDisplayMode('weekly')}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${
                    displayMode === 'weekly'
                      ? 'bg-terracotta text-white shadow-xs'
                      : 'text-parchment-700 hover:bg-parchment-100'
                  }`}
                >
                  <List className="w-3.5 h-3.5" />
                  <span>Weekly</span>
                </button>
                <button
                  onClick={() => setDisplayMode('monthly')}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${
                    displayMode === 'monthly'
                      ? 'bg-terracotta text-white shadow-xs'
                      : 'text-parchment-700 hover:bg-parchment-100'
                  }`}
                >
                  <CalendarIcon className="w-3.5 h-3.5" />
                  <span>Monthly</span>
                </button>
              </div>
            </div>
          </div>

          {/* BATCH ACTION CONTROLS */}
          {filteredItems.length > 0 && (
            <div className="flex items-center justify-between bg-white px-4 py-2 rounded-2xl border border-parchment-200 shadow-sm text-xs font-medium text-parchment-800">
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

          {/* MAIN CALENDAR DISPLAY */}
          {loading ? (
            <div className="py-20 text-center text-xs text-parchment-600 animate-pulse">
              Loading calendar schedules...
            </div>
          ) : filteredItems.length === 0 || activeDaysList.length === 0 ? (
            <div className="py-16 bg-white rounded-3xl border border-dashed border-parchment-200 text-center p-6 space-y-2">
              <Sparkles className="w-8 h-8 text-terracotta/40 mx-auto" />
              <h3 className="font-serif font-bold text-parchment-900">
                {upcomingOnly ? 'No upcoming events saved' : 'No events saved under this vibe yet'}
              </h3>
              <p className="text-xs text-parchment-700 max-w-xs mx-auto">
                Head back to the deck to start exploring and picking your local vibes.
              </p>
            </div>
          ) : displayMode === 'weekly' ? (
            
            /* WEEKLY VIEW */
            <div className="space-y-4">
              {activeDaysList.map((day) => (
                <div
                  key={day.dateKey}
                  className={`p-4 rounded-3xl border bg-white shadow-xs space-y-3 ${
                    day.isToday ? 'border-amber-400 ring-1 ring-amber-300' : 'border-parchment-200'
                  }`}
                >
                  <div className="flex items-center justify-between border-b border-parchment-100 pb-2">
                    <span className="text-xs font-bold text-parchment-900 uppercase tracking-wide">
                      {day.dateObj.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })}
                    </span>
                    <span className="text-[10px] bg-terracotta/10 text-terracotta font-bold px-2 py-0.5 rounded-full">
                      {day.entries.length} Event{day.entries.length > 1 ? 's' : ''}
                    </span>
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    {day.entries.map((entry, idx) => {
                      const ev = entry.event || {};
                      const venue = ev.venue_name || ev.venue || 'Local Venue';
                      const isSelected = selectedIds.includes(entry.historyId);

                      return (
                        <div
                          key={`${entry.historyId}-${idx}`}
                          onClick={() => setActiveModalEvent({ ...entry, dateKey: day.dateKey })}
                          className={`relative bg-parchment-50/50 rounded-2xl border p-3 flex gap-3 hover:border-terracotta cursor-pointer transition-all ${
                            isSelected ? 'border-terracotta ring-1 ring-terracotta' : 'border-parchment-200/80'
                          }`}
                        >
                          <button
                            onClick={(e) => toggleSelect(entry.historyId, e)}
                            className="absolute top-2 right-2 z-10 bg-white/90 p-1 rounded-lg border border-parchment-200"
                          >
                            {isSelected ? (
                              <CheckSquare className="w-3.5 h-3.5 text-terracotta" />
                            ) : (
                              <Square className="w-3.5 h-3.5 text-parchment-400" />
                            )}
                          </button>

                          <img
                            src={ev.image_url || FALLBACK_IMAGE}
                            alt={ev.title || 'Event'}
                            className="w-16 h-16 object-cover rounded-xl shrink-0 bg-parchment-100 border border-parchment-100"
                            onError={(e) => { e.target.onerror = null; e.target.src = FALLBACK_IMAGE; }}
                          />

                          <div className="flex-1 min-w-0 flex flex-col justify-between space-y-1">
                            <div>
                              <h4 className="font-serif font-bold text-xs text-parchment-900 leading-snug line-clamp-1 pr-6">
                                {ev.title || 'Saved Event'}
                              </h4>
                              <p className="text-[11px] text-parchment-600 flex items-center gap-1 line-clamp-1 mt-0.5">
                                <MapPin className="w-3 h-3 shrink-0" />
                                <span className="truncate">{venue}</span>
                              </p>
                            </div>

                            <span className="self-start inline-flex items-center gap-1 text-[11px] font-bold text-terracotta hover:underline mt-1">
                              <span>Click to Expand</span>
                              <Sparkles className="w-3 h-3" />
                            </span>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          ) : (

            /* MONTHLY GRID VIEW */
            <div className="bg-white rounded-3xl border border-parchment-200 shadow-sm p-4 space-y-4">
              
              {/* MONTH NAVIGATOR HEADER */}
              <div className="flex items-center justify-between border-b border-parchment-100 pb-3">
                <h2 className="font-serif font-bold text-base text-parchment-900">
                  {currentMonthDate.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })}
                </h2>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => setCurrentMonthDate((prev) => new Date(prev.getFullYear(), prev.getMonth() - 1, 1))}
                    className="p-1.5 text-parchment-700 hover:bg-parchment-100 rounded-xl transition-colors"
                  >
                    <ChevronLeft className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => setCurrentMonthDate(new Date())}
                    className="text-xs font-semibold text-terracotta px-2 py-1 rounded-lg hover:bg-terracotta/10 transition-colors"
                  >
                    Today
                  </button>
                  <button
                    onClick={() => setCurrentMonthDate((prev) => new Date(prev.getFullYear(), prev.getMonth() + 1, 1))}
                    className="p-1.5 text-parchment-700 hover:bg-parchment-100 rounded-xl transition-colors"
                  >
                    <ChevronRight className="w-4 h-4" />
                  </button>
                </div>
              </div>

              {/* DAY HEADERS */}
              <div className="grid grid-cols-7 gap-1 text-center font-bold text-[11px] text-parchment-500 uppercase tracking-wider">
                {['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map((d) => (
                  <span key={d}>{d}</span>
                ))}
              </div>

              {/* CALENDAR MONTH GRID */}
              <div className="grid grid-cols-7 gap-1 auto-rows-fr">
                {monthlyGrid.map((cell) => (
                  <div
                    key={cell.isPadding ? cell.id : cell.dateKey}
                    className={`min-h-[80px] p-1.5 rounded-2xl border flex flex-col justify-between transition-colors ${
                      cell.isPadding 
                        ? 'bg-parchment-50/20 border-transparent' 
                        : 'bg-parchment-50/50 border-parchment-100'
                    }`}
                  >
                    {!cell.isPadding && (
                      <>
                        <span className="text-[10px] font-bold text-parchment-600 self-start">
                          {cell.dayNumber}
                        </span>

                        <div className="space-y-1 mt-1 overflow-y-auto max-h-[55px] no-scrollbar">
                          {cell.entries.map((entry, eIdx) => (
                            <button
                              key={`${entry.historyId}-${eIdx}`}
                              onClick={() => setActiveModalEvent({ ...entry, dateKey: cell.dateKey })}
                              className="w-full text-left text-[10px] leading-tight font-bold text-terracotta bg-terracotta/10 hover:bg-terracotta hover:text-white px-1.5 py-0.5 rounded-lg truncate transition-all"
                              title={entry.event?.title || 'Saved Event'}
                            >
                              {entry.event?.title || 'Saved Event'}
                            </button>
                          ))}
                        </div>
                      </>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </main>

      {/* EVENT DETAIL POP-UP MODAL OVERLAY */}
      {activeModalEvent && (() => {
        const ev = activeModalEvent.event || {};
        const venue = ev.venue_name || ev.venue || 'Local Venue';
        const mapUrl = ev.latitude && ev.longitude 
          ? `https://www.google.com/maps/search/?api=1&query=${ev.latitude},${ev.longitude}`
          : `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(venue + ' ' + (ev.address || 'Tacoma, WA'))}`;

        return (
          <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4 animate-in fade-in duration-200">
            <div className="bg-white rounded-3xl max-w-lg w-full max-h-[90dvh] overflow-y-auto border border-parchment-200 shadow-2xl flex flex-col relative animate-in zoom-in-95 duration-200">
              
              {/* CLOSE BUTTON */}
              <button
                onClick={() => setActiveModalEvent(null)}
                className="absolute top-3 right-3 z-20 bg-white/80 backdrop-blur-md p-2 rounded-full text-parchment-700 hover:text-terracotta hover:bg-white shadow-md transition-colors"
                aria-label="Close modal"
              >
                <X className="w-5 h-5" />
              </button>

              {/* MODAL IMAGE */}
              <div className="relative h-56 w-full bg-parchment-100 shrink-0">
                <img
                  src={ev.image_url || FALLBACK_IMAGE}
                  alt={ev.title || 'Event'}
                  className="w-full h-full object-cover"
                  onError={(e) => { e.target.onerror = null; e.target.src = FALLBACK_IMAGE; }}
                />
                <div className="absolute inset-0 bg-gradient-to-t from-black/60 via-transparent to-transparent" />
                <span className="absolute bottom-3 left-4 text-xs font-bold uppercase tracking-wider bg-black/50 text-white px-2.5 py-1 rounded-full border border-white/20 backdrop-blur-md">
                  {ev.category || 'Local Vibe'}
                </span>
              </div>

              {/* MODAL CONTENT BODY */}
              <div className="p-6 space-y-4 flex-1">
                <div>
                  <h3 className="font-serif font-bold text-xl text-parchment-900 leading-tight">
                    {ev.title || 'Saved Event'}
                  </h3>
                  <a
                    href={mapUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1.5 text-xs text-terracotta font-medium hover:underline mt-1"
                  >
                    <MapPin className="w-3.5 h-3.5 shrink-0" />
                    <span>{venue} {ev.address ? `• ${ev.address}` : ''}</span>
                  </a>
                </div>

                {/* FULL DESCRIPTION */}
                <p className="text-xs sm:text-sm text-parchment-800 leading-relaxed bg-parchment-50 p-3.5 rounded-2xl border border-parchment-100">
                  {ev.description || 'No extended description available for this event.'}
                </p>

                {/* METADATA TAGS */}
                <div className="flex flex-wrap gap-2 text-xs">
                  {ev.is_free && (
                    <span className="bg-emerald-100 text-emerald-800 font-bold px-2.5 py-1 rounded-full flex items-center gap-1">
                      <Tag className="w-3 h-3" /> Free Event
                    </span>
                  )}
                  {ev.age_restriction && (
                    <span className="bg-parchment-100 text-parchment-800 font-semibold px-2.5 py-1 rounded-full">
                      {ev.age_restriction}
                    </span>
                  )}
                  {ev.indoor_outdoor && (
                    <span className="bg-parchment-100 text-parchment-800 font-semibold px-2.5 py-1 rounded-full">
                      {ev.indoor_outdoor}
                    </span>
                  )}
                </div>

                {/* DIRECT ACTION LINK */}
                <div className="pt-2 flex justify-end">
                  <a
                    href={activeModalEvent.url || ev.external_url || '#'}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="w-full sm:w-auto inline-flex items-center justify-center gap-2 bg-terracotta text-white font-bold px-5 py-3 rounded-2xl hover:bg-terracotta-hover transition-colors shadow-lg"
                  >
                    <span>Get Tickets & Details</span>
                    <ExternalLink className="w-4 h-4" />
                  </a>
                </div>
              </div>
            </div>
          </div>
        );
      })()}
    </div>
  );
}