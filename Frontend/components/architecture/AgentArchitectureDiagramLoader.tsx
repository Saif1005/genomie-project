'use client';

import dynamic from 'next/dynamic';

/** SSR-safe wrapper: Mermaid never runs on the server */
const AgentArchitectureDiagram = dynamic(
  () => import('./AgentArchitectureDiagram'),
  {
    ssr: false,
    loading: () => (
      <div
        style={{
          padding: '2rem',
          textAlign: 'center',
          color: '#94a3b8',
          background: 'rgba(15,23,42,0.85)',
          borderRadius: '1.25rem',
          border: '1px solid rgba(255,255,255,0.08)',
        }}
      >
        Loading the architecture diagram…
      </div>
    ),
  }
);

export default AgentArchitectureDiagram;
