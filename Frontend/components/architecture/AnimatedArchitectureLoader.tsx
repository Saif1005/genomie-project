'use client';

import dynamic from 'next/dynamic';

/**
 * SSR-safe import: avoids `window is not defined` during build/prerender.
 */
const AnimatedArchitecture = dynamic(() => import('./AnimatedArchitecture'), {
  ssr: false,
  loading: () => (
    <div
      style={{
        padding: '2rem',
        textAlign: 'center',
        color: '#94a3b8',
        background: 'rgba(15,23,42,0.9)',
        borderRadius: '1.25rem',
        border: '1px solid rgba(255,255,255,0.08)',
      }}
    >
      Loading the animated diagram…
    </div>
  ),
});

export default AnimatedArchitecture;
