import React, { useState, useEffect, useRef } from 'react';
import { SlidersHorizontal, Bookmark, History, ChevronUp, Sparkles } from 'lucide-react';
import MoodSliders from './MoodSliders';

export default function Header({ mood, onChangeMood, onOpenHistory, onOpenSaved, bookmarkCount, hasUserHash }) {
  const [isSlidersOpen, setIsSlidersOpen] = useState(false);
  const collapseTimeoutRef = useRef(null);

  const handleMoodChange = (newMood) => {
    onChangeMood(newMood);

    // Auto-collapse after 2.5 seconds of inactivity
    if (collapseTimeoutRef.current) clearTimeout(collapseTimeoutRef.current);
    collapseTimeoutRef.current = setTimeout(() => {
      setIsSlidersOpen(false);
    }, 2500);
  };

  useEffect(() => {
    return () => {
      if (collapseTimeoutRef.current) clearTimeout(collapseTimeoutRef.current);
    };
  }, []);

  return (
    <header className="sticky top-0 z-40 bg-parchment-50/95 backdrop-blur-md border-b border-parchment-200 shadow-sm">
      <div className="max-w-md mx-auto px-4 py-3 flex items-center justify-between">
        {/* Logo / Brand */}
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-xl bg-terracotta text-white flex items-center justify-center shadow-sm">
            <Sparkles className="w-4 h-4 fill-white" />
          </div>
          <span className="font-serif font-bold text-lg tracking-tight text-parchment-900">
            Vivida
          </span>
        </div>

        {/* Action Controls */}
        <div className="flex items-center gap-2">
          {/* Mood Slider Toggle */}
          <button
            type="button"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              setIsSlidersOpen((prev) => !prev);
            }}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold border transition-all ${
              isSlidersOpen
                ? 'bg-terracotta text-white border-terracotta shadow-sm'
                : 'bg-white text-parchment-800 border-parchment-200 hover:border-terracotta/50'
            }`}
            title="Adjust Mood Vibe"
          >
            <SlidersHorizontal className="w-3.5 h-3.5" />
            <span>Vibe</span>
          </button>

          {/* Saved Events Button */}
          <button
            type="button"
            onClick={(e) => {
              e.preventDefault();
              onOpenSaved();
            }}
            className="relative p-2 rounded-full bg-white border border-parchment-200 text-parchment-800 hover:bg-parchment-100 transition-colors"
            title="Saved Events"
          >
            <Bookmark className="w-4 h-4" />
            {bookmarkCount > 0 && (
              <span className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-terracotta text-white text-[10px] font-bold flex items-center justify-center shadow-xs">
                {bookmarkCount}
              </span>
            )}
          </button>

          {/* History Button */}
          <button
            type="button"
            onClick={(e) => {
              e.preventDefault();
              onOpenHistory();
            }}
            className="p-2 rounded-full bg-white border border-parchment-200 text-parchment-800 hover:bg-parchment-100 transition-colors"
            title="Interaction History"
          >
            <History className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Collapsible Drawer Area */}
      {isSlidersOpen && (
        <div className="border-t border-parchment-200/60 bg-parchment-100/90 px-4 py-3 shadow-inner">
          <div className="max-w-md mx-auto">
            <MoodSliders mood={mood} onChange={handleMoodChange} />
            <button
              type="button"
              onClick={() => setIsSlidersOpen(false)}
              className="mt-2 w-full flex items-center justify-center gap-1 text-[11px] font-medium text-parchment-700 hover:text-terracotta pt-1"
            >
              <span>Collapse controls</span>
              <ChevronUp className="w-3 h-3" />
            </button>
          </div>
        </div>
      )}
    </header>
  );
}