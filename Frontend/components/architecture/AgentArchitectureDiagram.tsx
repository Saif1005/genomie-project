'use client';

import { useCallback, useEffect, useId, useRef, useState } from 'react';
import {
  ARCHITECTURE_MERMAID,
  WORKFLOW_STEPS,
  type EdgePair,
  type WorkflowStep,
} from '@/lib/mermaid/architectureDiagram';
import { annotateDataFlowPaths } from '@/lib/mermaid/annotateEdges';
import styles from './AgentArchitectureDiagram.module.css';

type MermaidAPI = typeof import('mermaid').default;

const PARTICLE_COLOR = '#0ea5e9';
const STEP_MS = 1800;

type Particle = {
  el: SVGCircleElement;
  path: SVGPathElement;
  len: number;
  offset: number;
  speed: number;
};

function edgeMatches(edgeId: string, from: string, to: string): boolean {
  const id = edgeId.toUpperCase();
  return id.includes(from.toUpperCase()) && id.includes(to.toUpperCase());
}

function findActivePath(
  container: HTMLElement,
  pairs: EdgePair[]
): SVGPathElement | null {
  for (const group of container.querySelectorAll<SVGGElement>('.edgePaths g.edgePath')) {
    if (pairs.some(([from, to]) => edgeMatches(group.id ?? '', from, to))) {
      return group.querySelector('path');
    }
  }
  return null;
}

function clearParticleLayer(container: HTMLElement) {
  container.querySelector('#particleLayer')?.remove();
}

function spawnParticles(
  svg: SVGSVGElement,
  path: SVGPathElement,
  label: string
): Particle[] {
  let layer = svg.querySelector<SVGGElement>('#particleLayer');
  if (!layer) {
    layer = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    layer.setAttribute('id', 'particleLayer');
    layer.setAttribute('class', styles.particleLayer);
    svg.appendChild(layer);
  }

  const len = path.getTotalLength();
  if (len < 10) return [];

  const mid = path.getPointAtLength(len * 0.5);
  const mid2 = path.getPointAtLength(Math.min(len * 0.5 + 4, len));
  const angle =
    (Math.atan2(mid2.y - mid.y, mid2.x - mid.x) * 180) / Math.PI;
  const text = `⇢ ${label}`;
  const w = text.length * 6.5 + 16;

  const labelG = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  labelG.setAttribute('transform', `translate(${mid.x},${mid.y}) rotate(${angle})`);

  const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
  rect.setAttribute('x', String(-w / 2));
  rect.setAttribute('y', '-22');
  rect.setAttribute('width', String(w));
  rect.setAttribute('height', '20');
  rect.setAttribute('rx', '6');
  rect.setAttribute('fill', 'rgba(2,6,23,0.92)');
  rect.setAttribute('stroke', PARTICLE_COLOR);
  rect.setAttribute('stroke-width', '1.5');

  const txt = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  txt.setAttribute('text-anchor', 'middle');
  txt.setAttribute('x', '0');
  txt.setAttribute('y', '-8');
  txt.setAttribute('fill', '#e0f2fe');
  txt.setAttribute('font-size', '11');
  txt.setAttribute('font-weight', '700');
  txt.setAttribute('font-family', 'ui-monospace, monospace');
  txt.textContent = text;

  labelG.appendChild(rect);
  labelG.appendChild(txt);
  layer.appendChild(labelG);

  const count = 5;
  const particles: Particle[] = [];
  for (let i = 0; i < count; i++) {
    const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    circle.setAttribute('r', i === 0 ? '7' : i === 1 ? '4' : '3');
    circle.setAttribute('fill', PARTICLE_COLOR);
    circle.setAttribute('opacity', i === 0 ? '1' : String(0.35 + (count - i) * 0.1));
    layer.appendChild(circle);
    particles.push({
      el: circle,
      path,
      len,
      offset: i / count,
      speed: 0.008 * (i === 0 ? 1 : 0.92),
    });
  }
  return particles;
}

export default function AgentArchitectureDiagram() {
  const reactId = useId().replace(/:/g, '');
  const hostRef = useRef<HTMLDivElement>(null);
  const mermaidRef = useRef<MermaidAPI | null>(null);
  const rafRef = useRef<number | null>(null);
  const particlesRef = useRef<Particle[]>([]);
  const runningRef = useRef(false);

  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [stepIndex, setStepIndex] = useState(-1);

  const stopParticles = useCallback(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    particlesRef.current = [];
    if (hostRef.current) clearParticleLayer(hostRef.current);
  }, []);

  const applyStep = useCallback(
    (step: WorkflowStep | null) => {
      if (!hostRef.current) return;
      annotateDataFlowPaths(hostRef.current, step?.highlight ?? []);
      stopParticles();

      if (!step) return;
      const svg = hostRef.current.querySelector('svg');
      const path = findActivePath(hostRef.current, step.highlight);
      if (svg && path) {
        particlesRef.current = spawnParticles(svg, path, step.transport);
        const tick = () => {
          particlesRef.current.forEach((p) => {
            p.offset = (p.offset + p.speed) % 1;
            const pt = p.path.getPointAtLength(p.offset * p.len);
            p.el.setAttribute('cx', String(pt.x));
            p.el.setAttribute('cy', String(pt.y));
          });
          rafRef.current = requestAnimationFrame(tick);
        };
        rafRef.current = requestAnimationFrame(tick);
      }
    },
    [stopParticles]
  );

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const mermaid = (await import('mermaid')).default;
        if (cancelled) return;

        mermaid.initialize({
          startOnLoad: false,
          theme: 'dark',
          securityLevel: 'loose',
          themeVariables: {
            primaryColor: '#0c4a6e',
            primaryTextColor: '#e0f2fe',
            primaryBorderColor: '#0ea5e9',
            lineColor: '#334155',
            secondaryColor: '#1e293b',
            tertiaryColor: '#020617',
            clusterBkg: '#0f172a',
            clusterBorder: '#334155',
            titleColor: '#94a3b8',
            edgeLabelBackground: '#1e293b',
          },
          flowchart: { curve: 'basis', htmlLabels: true, padding: 16 },
        });

        mermaidRef.current = mermaid;
        const { svg } = await mermaid.render(`archi-${reactId}`, ARCHITECTURE_MERMAID);
        if (cancelled || !hostRef.current) return;

        hostRef.current.innerHTML = svg;
        annotateDataFlowPaths(hostRef.current, []);
        setReady(true);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Erreur rendu Mermaid');
        }
      }
    })();

    return () => {
      cancelled = true;
      stopParticles();
    };
  }, [reactId, stopParticles]);

  const runSession = useCallback(async () => {
    if (!ready || runningRef.current) return;
    runningRef.current = true;
    setPlaying(true);

    for (let i = 0; i < WORKFLOW_STEPS.length; i++) {
      setStepIndex(i);
      applyStep(WORKFLOW_STEPS[i]);
      await new Promise((r) => setTimeout(r, STEP_MS));
    }

    runningRef.current = false;
    setPlaying(false);
  }, [ready, applyStep]);

  const reset = useCallback(() => {
    stopParticles();
    setStepIndex(-1);
    applyStep(null);
  }, [applyStep, stopParticles]);

  const currentStep = stepIndex >= 0 ? WORKFLOW_STEPS[stepIndex] : null;

  return (
    <section className={styles.root}>
      <div className={styles.header}>
        <div>
          <div className={styles.title}>Architecture multi-agents — Projet Zaynb</div>
          <div className={styles.subtitle}>
            FASTQ → VCF → ClinVar → risque · orchestrateur LangGraph · serveur local
          </div>
        </div>
        <div className={styles.controls}>
          <span className={styles.status}>
            {playing ? 'SESSION EN COURS' : ready ? 'PRÊT' : 'CHARGEMENT…'}
          </span>
          <button
            type="button"
            className={styles.btn}
            disabled={!ready || playing}
            onClick={runSession}
          >
            ▶ Démarrer la session
          </button>
          <button
            type="button"
            className={styles.btn}
            disabled={!ready || playing}
            onClick={reset}
            style={{ background: '#1e293b' }}
          >
            Réinitialiser
          </button>
        </div>
      </div>

      {error && <p className={styles.error}>{error}</p>}
      {!ready && !error && <p className={styles.loading}>Rendu Mermaid côté client…</p>}

      <div ref={hostRef} className={styles.diagramHost} aria-live="polite" />

      <div
        className={`${styles.banner} ${currentStep ? styles.bannerActive : ''}`}
      >
        {currentStep
          ? `Propagation : ${currentStep.transport} — ${currentStep.dataLabel}`
          : 'Flèches agents en attente — lancez la session pour voir le flux de données'}
      </div>

      <div className={styles.legend}>
        <div className={styles.legendItem}>
          <span className={styles.legendSwatchAgent} />
          Flux agents (stroke-dashoffset animé)
        </div>
        <div className={styles.legendItem}>
          <span className={styles.legendSwatchPipeline} />
          Pipeline génomique — serveur local
        </div>
      </div>

      {currentStep && (
        <aside className={styles.sidebar}>
          <div className={styles.sidebarTag}>
            Étape {stepIndex + 1}/{WORKFLOW_STEPS.length} — {currentStep.tag}
          </div>
          <div className={styles.sidebarTitle}>{currentStep.title}</div>
          <p className={styles.sidebarDesc}>{currentStep.description}</p>
        </aside>
      )}
    </section>
  );
}
