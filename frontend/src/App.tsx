import React from 'react'
import './App.css'

export const App: React.FC = () => {
  return (
    <div className="cadence-container">
      <header className="cadence-header">
        <div className="brand-badge">
          <span>RAILWAY ENGINE</span>
          <span>//</span>
          <span>BLOCK DISPATCH</span>
        </div>
        <div className="status-badge">
          <span className="status-dot"></span>
          <span>Status: under active development</span>
        </div>
      </header>

      <main className="hero-content">
        <svg
          className="railway-symbol"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.75"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z" />
          <line x1="4" y1="22" x2="4" y2="15" />
          <path d="M2 20h20" />
          <path d="M6 18l1.5 4" />
          <path d="M18 18l-1.5 4" />
          <path d="M12 18v4" />
        </svg>

        <h1 className="cadence-title">Cadence</h1>
        <p className="cadence-tagline">
          It doesn't predict delays. It decides when the track is free.
        </p>

        <div className="track-visualization" role="region" aria-label="Block Window Visualization">
          <div className="block-indicator">
            <span className="block-bar active"></span>
            <span>BLK-01</span>
          </div>
          <span className="block-arrow">→</span>
          <div className="block-indicator">
            <span className="block-bar scheduled"></span>
            <span>WINDOW</span>
          </div>
          <span className="block-arrow">→</span>
          <div className="block-indicator">
            <span className="block-bar"></span>
            <span>BLK-02</span>
          </div>
        </div>

        <div className="info-cards-grid">
          <div className="info-card">
            <div className="card-label">Core Engine</div>
            <div className="card-value">FastAPI + OR-Tools</div>
            <div className="card-desc">Constraint programming & network-flow block allocation.</div>
          </div>
          <div className="info-card">
            <div className="card-label">Client Interface</div>
            <div className="card-value">React + TypeScript</div>
            <div className="card-desc">Real-time scheduling topology and window occupancy views.</div>
          </div>
          <div className="info-card">
            <div className="card-label">System State</div>
            <div className="card-value">Scaffold Initialized</div>
            <div className="card-desc">Monorepo configured with verified automated CI workflows.</div>
          </div>
        </div>
      </main>

      <footer className="cadence-footer">
        <div className="footer-left">Cadence &copy; 2026 jeevapriyan10</div>
        <div>Railway Block-Window Scheduling Engine</div>
      </footer>
    </div>
  )
}

export default App
