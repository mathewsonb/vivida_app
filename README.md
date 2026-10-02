# Vivida — The Hyper-Local Mood Engine 🌿

> **Less Searching. More Living.**
> *An anti-slop, privacy-first local experience engine powered by triaxial mood vectors, ephemeral session keys, and zero-clutter swiping.*

---

## 📖 Overview

**Vivida** is a hyper-local event and experience discovery platform designed to solve choice paralysis. Instead of overwhelming users with endless directories, comment spam, and advertisement clutter, Vivida matches users with tailored local experiences based on a **Triaxial Mood Vector** ($Energy$, $Social$, $Novelty$).

Built around the philosophy that you shouldn't have to navigate a complex directory just to find something great to do tonight, Vivida provides a clean, 3-button swipe interface (*"Not My Scene"*, *"Interested"*, *"Totally My Vibe"*) that guides users effortlessly toward experiences that actually fit their current mood.

---

## ✨ Key Features & Architecture

* **Triaxial Vector Match Scoring:** Computes dynamic match scores ($1 - \frac{\Vert{}U - E\Vert{}}{\sqrt{3}}$) between the user's mood vector and event profiles in real time.
* **3-Tier Implicit Intent Deck:** Card deck driven by Framer Motion gestures with instant visual feedback and card-deck cycling.
* **Privacy-First Dual-Identity Engine:**
  * **Ephemeral Session Layer:** Tracks low-friction swipes using 6-month recycled session IDs. No accounts, no cross-site tracking cookies.
  * **Longitudinal Identity Layer:** Anchors user profiles using salted, normalized SHA-256 email hashes ($SHA256(Lowercase(Trim(Email)) + APP\_SALT)$) triggered *only* upon explicit conversion ("Totally My Vibe").
* **Targeted Purges (GDPR/CCPA Native):** Allows immediate execution of "Right to be Forgotten" requests without corrupting historical vector training data.
* **Batch Share & Native OS Integration:** Generates self-contained, anonymous batch-invite tokens allowing 1-click event sharing via Web Share API (`navigator.share`), WhatsApp, SMS, or Email.
* **Reconstructable Client History:** Allows users to export their complete interaction timeline securely via deterministic hash reconstruction and magic link delivery.
* **Dynamic Calendar Generation:** Instant `.ics` dynamic webcal file exports for seamless integration with Apple Calendar, Google Calendar, and Outlook.

---

## 🛠️ Technology Stack

* **Frontend:** React 18, Tailwind CSS, Framer Motion, Lucide Icons.
* **Database & BaaS:** Supabase (PostgreSQL + Serverless Edge Functions).
* **Data Harvesting Pipeline:** Python, Playwright / BeautifulSoup, Scheduled GitHub Actions Crons.
* **Inference & Vectorization:** Lightweight LLM parsing for unstructured city permits, vendor feeds, and schema markup (`JSON-LD`).
* **Hosting:** GitHub Pages / Cloudflare Pages (Zero-cost static edge hosting).

---

## 📁 Repository Structure

```text
vivida_app/
├── .github/
│   └── workflows/
│       ├── deploy.yaml          # Actions workflow for GitHub Pages deployment
│       └── ingest.yaml          # Scheduled daily Python ETL & Gemini vectorizer pipeline
├── src/
│   ├── components/
│   │   ├── EmailPromptModal.jsx # Client-side email hashing modal
│   │   ├── Header.jsx           # App header with mood slider drawer toggle & counters
│   │   ├── HistoryModal.jsx     # Saved history, event invite generator, & export modal
│   │   ├── HistoryPage.jsx      # Full-page interactive selection history manager
│   │   ├── MoodSliders.jsx      # Energy, Social, & Novelty control sliders
│   │   ├── SwipeDeck.jsx        # Framer Motion card deck with swipe gestures
│   │   └── UserHistoryReport.jsx# Dynamic Vibe Profile report & email digest trigger
│   ├── hooks/
│   │   └── useEmailHash.js      # Utility for generating salted SHA-256 hashes
│   ├── lib/
│   │   ├── crypto.js            # SHA-256 email hashing functions using Web Crypto API
│   │   ├── supabaseClient.js    # Supabase JS client initializer
│   │   └── tracking.js          # Direct event interaction logger for Supabase
│   ├── services/
│   │   └── historyService.js    # Vector drift calculations, local storage, & DB sync
│   ├── utils/
│   │   └── session.js           # Client ID generator/retriever
│   ├── App.jsx                  # Main application orchestrator & state manager
│   ├── index.css                # Tailwind CSS base styles & overscroll behavior
│   └── main.jsx                 # React root DOM entrypoint
├── index.html                   # HTML template & Web Font dependencies
├── package-lock.json            # Lockfile detailing dependencies (React 19, Vite, Framer Motion)
└── app-schema.txt               # Aggregated project code repository reference
```
---

## 🚀 Quickstart

1. **Install dependencies:**
   ```bash
   npm install
   cd scraper && pip install -r requirements.txt && cd ..
