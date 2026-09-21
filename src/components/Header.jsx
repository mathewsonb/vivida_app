import React from 'react';
import { Sparkles, Bookmark, Clock } from 'lucide-react';

export default function Header({ onOpenBookmarks, onOpenHistory, bookmarkCount = 0 }) {
  return (
    <header className="sticky top-0 z-30 bg-parchment-50/90 backdrop-blur-md border-b border-parchment-200 px-4 py-3 transition-colors duration-300">
      <div className="max-w-md mx-auto flex items-center justify-between">
        {/* Left: History/Reconstruction Trigger */}
        <button
          onClick={onOpenHistory}
          className="p-2 rounded-full hover:bg-parchment-100 text-parchment-800 transition-colors relative"
          title="My Interaction History"
        >
          <Clock className="w-5 h-5" />
        </button>

        {/* Center: Concept 1 "Infinite Loop" Logo */}
        <div className="flex flex-col items-center">
          <div className="relative group cursor-pointer">
            <h1 className="font-serif text-2xl font-bold tracking-tight text-parchment-900 flex items-center gap-1">
              Vivida
              <span className="text-terracotta text-xs font-sans tracking-widest uppercase font-semibold">.app</span>
            </h1>
            {/* Embedded Concept 1 SVG Loop Flourish */}
            <svg 
              className="w-24 h-4 text-terracotta transition-transform duration-300 group-hover:scale-105" 
              viewBox="0 0 100 20" 
              fill="none" 
              stroke="currentColor" 
              strokeWidth="2" 
              strokeLinecap="round"
            >
              <path d="M 5 12 C 30 18, 70 18, 95 12 C 80 2, 40 22, 10 8" />
            </svg>
          </div>
        </div>

        {/* Right: Bookmarks Drawer Trigger */}
        <button
          onClick={onOpenBookmarks}
          className="p-2 rounded-full hover:bg-parchment-100 text-parchment-800 transition-colors relative"
          title="Saved Events"
        >
          <Bookmark className="w-5 h-5" />
          {bookmarkCount > 0 && (
            <span className="absolute top-1 right-1 bg-terracotta text-white text-[10px] font-bold w-4 h-4 rounded-full flex items-center justify-center">
              {bookmarkCount}
            </span>
          )}
        </button>
      </div>
    </header>
  );
}