import type { ArchWorkflowStep, NodePair } from './animatedArchitectureDiagram';

const DELEGATION_COLOR = '#0ea5e9';
const TOOL_COLOR = '#10b981';
const ONE_SHOT_COLOR = '#a78bfa';
const VALIDATION_COLOR = '#f59e0b';
const IDLE_COLOR = '#334155';
const DASH_ARRAY = '8 4';
const DASH_CYCLE = 12;

type FlowParticle = {
  el: SVGCircleElement;
  path: SVGPathElement;
  len: number;
  offset: number;
  speed: number;
};

type ActivePaths = {
  delegation: SVGPathElement[];
  tool: SVGPathElement[];
};

let rafId = 0;
let dashOffset = 0;
let particles: FlowParticle[] = [];

function toClassPair([from, to]: NodePair): [string, string] {
  return [`LS-${from}`, `LE-${to}`];
}

function findPathByNodes(container: HTMLElement, pair: NodePair): SVGPathElement | null {
  const [start, end] = toClassPair(pair);
  const paths = container.querySelectorAll<SVGPathElement>(
    '.edgePaths > path, g.edgePaths path.path'
  );

  for (const path of paths) {
    const cls = path.getAttribute('class') ?? '';
    if (cls.includes(start) && cls.includes(end)) return path;
  }
  return null;
}

function getAllEdgePaths(container: HTMLElement): SVGPathElement[] {
  return [...container.querySelectorAll<SVGPathElement>(
    '.edgePaths > path, g.edgePaths path.path'
  )];
}

function dimAllPaths(container: HTMLElement): void {
  getAllEdgePaths(container).forEach((path) => {
    path.classList.remove('data-flow-path', 'data-tool-path', 'flow-active');
    path.style.stroke = IDLE_COLOR;
    path.style.strokeWidth = '1.5px';
    path.style.strokeDasharray = 'none';
    path.style.strokeDashoffset = '0';
    path.style.opacity = '0.35';
    path.style.fill = 'none';
  });
}

function styleActivePath(
  path: SVGPathElement,
  color: string,
  kind: 'delegation' | 'tool' | 'one-shot' | 'validation'
): void {
  const cssKind = kind === 'tool' ? 'data-tool-path' : 'data-flow-path';
  path.classList.add('flow-active', cssKind);
  if (kind === 'one-shot') path.classList.add('data-one-shot-path');
  if (kind === 'validation') path.classList.add('data-validation-path');
  path.removeAttribute('style');
  path.style.stroke = color;
  path.style.strokeWidth =
    kind === 'one-shot' || kind === 'validation' ? '3.5px' : kind === 'tool' ? '3px' : '2.5px';
  path.style.fill = 'none';
  path.style.strokeDasharray =
    kind === 'one-shot' ? '10 6' : kind === 'validation' ? '6 4' : DASH_ARRAY;
  path.style.strokeLinecap = 'round';
  path.style.opacity = '1';
}

function clearParticleLayer(svg: SVGSVGElement): void {
  svg.querySelector('#flow-particle-layer')?.remove();
  svg.querySelector('#flow-label-layer')?.remove();
  svg.querySelector('#one-shot-badge-layer')?.remove();
  svg.querySelector('#validation-badge-layer')?.remove();
  svg.querySelectorAll('.one-shot-node-badge, .validation-node-badge').forEach((el) => el.remove());
}

function findNodeGroup(container: HTMLElement, nodeId: string): SVGGElement | null {
  const nodes = container.querySelectorAll<SVGGElement>('g.node');
  for (const node of nodes) {
    const id = node.id ?? '';
    if (id.toUpperCase().includes(nodeId.toUpperCase())) return node;
  }
  return null;
}

function addOneShotNodeBadge(svg: SVGSVGElement, container: HTMLElement, nodeId: string): void {
  const node = findNodeGroup(container, nodeId);
  if (!node) return;

  const bbox = (node as SVGGElement).getBBox();
  const cx = bbox.x + bbox.width / 2;
  const cy = bbox.y - 14;

  let layer = svg.querySelector<SVGGElement>('#one-shot-badge-layer');
  if (!layer) {
    layer = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    layer.setAttribute('id', 'one-shot-badge-layer');
    layer.setAttribute('pointer-events', 'none');
    svg.appendChild(layer);
  }

  const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  g.setAttribute('class', 'one-shot-node-badge');
  g.setAttribute('transform', `translate(${cx},${cy})`);

  const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
  rect.setAttribute('x', '-52');
  rect.setAttribute('y', '-11');
  rect.setAttribute('width', '104');
  rect.setAttribute('height', '22');
  rect.setAttribute('rx', '11');
  rect.setAttribute('fill', 'rgba(88,28,135,0.92)');
  rect.setAttribute('stroke', ONE_SHOT_COLOR);
  rect.setAttribute('stroke-width', '2');

  const txt = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  txt.setAttribute('text-anchor', 'middle');
  txt.setAttribute('y', '4');
  txt.setAttribute('fill', '#ede9fe');
  txt.setAttribute('font-size', '11');
  txt.setAttribute('font-weight', '800');
  txt.setAttribute('font-family', 'ui-monospace, monospace');
  txt.textContent = '1× ONE SHOT';

  g.appendChild(rect);
  g.appendChild(txt);
  layer.appendChild(g);
}

function addValidationNodeBadge(svg: SVGSVGElement, container: HTMLElement, nodeId: string): void {
  const node = findNodeGroup(container, nodeId);
  if (!node) return;

  const bbox = (node as SVGGElement).getBBox();
  const cx = bbox.x + bbox.width / 2;
  const cy = bbox.y - 14;

  let layer = svg.querySelector<SVGGElement>('#validation-badge-layer');
  if (!layer) {
    layer = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    layer.setAttribute('id', 'validation-badge-layer');
    layer.setAttribute('pointer-events', 'none');
    svg.appendChild(layer);
  }

  const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  g.setAttribute('class', 'validation-node-badge');
  g.setAttribute('transform', `translate(${cx},${cy})`);

  const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
  rect.setAttribute('x', '-58');
  rect.setAttribute('y', '-11');
  rect.setAttribute('width', '116');
  rect.setAttribute('height', '22');
  rect.setAttribute('rx', '11');
  rect.setAttribute('fill', 'rgba(120,53,15,0.92)');
  rect.setAttribute('stroke', VALIDATION_COLOR);
  rect.setAttribute('stroke-width', '2');

  const txt = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  txt.setAttribute('text-anchor', 'middle');
  txt.setAttribute('y', '4');
  txt.setAttribute('fill', '#fef3c7');
  txt.setAttribute('font-size', '11');
  txt.setAttribute('font-weight', '800');
  txt.setAttribute('font-family', 'ui-monospace, monospace');
  txt.textContent = '✓ VALIDATION';

  g.appendChild(rect);
  g.appendChild(txt);
  layer.appendChild(g);
}

function addPathLabel(
  svg: SVGSVGElement,
  path: SVGPathElement,
  text: string,
  color: string
): void {
  let layer = svg.querySelector<SVGGElement>('#flow-label-layer');
  if (!layer) {
    layer = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    layer.setAttribute('id', 'flow-label-layer');
    layer.setAttribute('pointer-events', 'none');
    svg.appendChild(layer);
  }

  const len = path.getTotalLength();
  const pt = path.getPointAtLength(len * 0.55);
  const pt2 = path.getPointAtLength(Math.min(len * 0.55 + 4, len));
  const angle = (Math.atan2(pt2.y - pt.y, pt2.x - pt.x) * 180) / Math.PI;
  const w = text.length * 6.2 + 18;

  const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  g.setAttribute('transform', `translate(${pt.x},${pt.y}) rotate(${angle})`);

  const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
  rect.setAttribute('x', String(-w / 2));
  rect.setAttribute('y', '-22');
  rect.setAttribute('width', String(w));
  rect.setAttribute('height', '20');
  rect.setAttribute('rx', '6');
  rect.setAttribute('fill', 'rgba(2,6,23,0.92)');
  rect.setAttribute('stroke', color);
  rect.setAttribute('stroke-width', '1.5');

  const txt = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  txt.setAttribute('text-anchor', 'middle');
  txt.setAttribute('x', '0');
  txt.setAttribute('y', '-8');
  txt.setAttribute('fill', '#ede9fe');
  txt.setAttribute('font-size', '10');
  txt.setAttribute('font-weight', '700');
  txt.setAttribute('font-family', 'ui-monospace, monospace');
  txt.textContent = text;

  g.appendChild(rect);
  g.appendChild(txt);
  layer!.appendChild(g);
}

function addToolLabel(svg: SVGSVGElement, path: SVGPathElement, text: string, oneShot = false): void {
  const label = oneShot ? `1× Tool: ${text}` : `⚙ Tool: ${text}`;
  addPathLabel(svg, path, label, oneShot ? ONE_SHOT_COLOR : TOOL_COLOR);
}

function spawnParticles(
  svg: SVGSVGElement,
  entries: { path: SVGPathElement; color: string }[]
): void {
  let layer = svg.querySelector<SVGGElement>('#flow-particle-layer');
  if (!layer) {
    layer = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    layer.setAttribute('id', 'flow-particle-layer');
    layer.setAttribute('pointer-events', 'none');
    svg.appendChild(layer);
  }
  layer.innerHTML = '';
  particles = [];

  entries.forEach(({ path, color }, i) => {
    const len = path.getTotalLength();
    if (len < 15) return;

    for (let j = 0; j < 4; j++) {
      const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      circle.setAttribute('r', j === 0 ? '6' : '3');
      circle.setAttribute('fill', color);
      circle.setAttribute('opacity', j === 0 ? '1' : '0.45');
      layer!.appendChild(circle);

      particles.push({
        el: circle,
        path,
        len,
        offset: (i * 0.1 + j * 0.22) % 1,
        speed: 0.005 + j * 0.0008,
      });
    }
  });
}

function startRaf(active: ActivePaths): void {
  cancelAnimationFrame(rafId);
  dashOffset = 0;

  const allActive = [...active.delegation, ...active.tool];

  const tick = () => {
    dashOffset = (dashOffset + 0.55) % DASH_CYCLE;
    allActive.forEach((path) => {
      path.style.strokeDashoffset = `${dashOffset}`;
    });

    particles.forEach((p) => {
      p.offset = (p.offset + p.speed) % 1;
      const pt = p.path.getPointAtLength(p.offset * p.len);
      p.el.setAttribute('cx', String(pt.x));
      p.el.setAttribute('cy', String(pt.y));
    });

    rafId = requestAnimationFrame(tick);
  };

  rafId = requestAnimationFrame(tick);
}

/** A single traversal of the arrow (0→1), then stop — indicates a one-shot execution */
function startOneShotRaf(active: ActivePaths): void {
  cancelAnimationFrame(rafId);
  particles = [];

  const allActive = [...active.delegation, ...active.tool];
  if (!allActive.length) return;

  const svg = allActive[0].ownerSVGElement;
  if (!svg) return;

  let layer = svg.querySelector<SVGGElement>('#flow-particle-layer');
  if (!layer) {
    layer = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    layer.setAttribute('id', 'flow-particle-layer');
    layer.setAttribute('pointer-events', 'none');
    svg.appendChild(layer);
  }
  layer.innerHTML = '';

  const shots: { path: SVGPathElement; len: number; circle: SVGCircleElement }[] = [];
  allActive.forEach((path) => {
    const len = path.getTotalLength();
    if (len < 15) return;
    const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    circle.setAttribute('r', '8');
    circle.setAttribute('fill', ONE_SHOT_COLOR);
    circle.setAttribute('opacity', '1');
    layer!.appendChild(circle);
    shots.push({ path, len, circle });
  });

  let progress = 0;

  const tick = () => {
    progress = Math.min(progress + 0.008, 1);

    allActive.forEach((path) => {
      const len = path.getTotalLength();
      path.style.strokeDashoffset = `${len * (1 - progress)}`;
    });

    shots.forEach(({ path, len, circle }) => {
      const pt = path.getPointAtLength(progress * len);
      circle.setAttribute('cx', String(pt.x));
      circle.setAttribute('cy', String(pt.y));
      circle.setAttribute('opacity', progress >= 0.98 ? '0.35' : '1');
    });

    if (progress < 1) {
      rafId = requestAnimationFrame(tick);
    }
  };

  rafId = requestAnimationFrame(tick);
}

function stopRaf(): void {
  cancelAnimationFrame(rafId);
  rafId = 0;
  particles = [];
}

export function applyWorkflowStep(
  container: HTMLElement,
  step: ArchWorkflowStep
): void {
  const svg = container.querySelector('svg');
  if (!svg) return;

  dimAllPaths(container);
  clearParticleLayer(svg);

  const active: ActivePaths = { delegation: [], tool: [] };

  const isOneShot = step.oneShot === true;
  const isValidation = step.orchestratorValidation === true;
  const flowColor = isOneShot
    ? ONE_SHOT_COLOR
    : isValidation
      ? VALIDATION_COLOR
      : DELEGATION_COLOR;
  const toolColor = isOneShot ? ONE_SHOT_COLOR : TOOL_COLOR;

  const delPath = findPathByNodes(container, step.delegation);
  if (delPath) {
    const delKind = isOneShot ? 'one-shot' : isValidation ? 'validation' : 'delegation';
    styleActivePath(delPath, flowColor, delKind);
    active.delegation.push(delPath);
    if (isOneShot) {
      addPathLabel(svg, delPath, '1× single delegation', ONE_SHOT_COLOR);
    } else if (isValidation) {
      addPathLabel(svg, delPath, '✓ Orchestrator validation', VALIDATION_COLOR);
    }
  }

  if (step.tool) {
    const toolPath = findPathByNodes(container, step.tool);
    if (toolPath) {
      styleActivePath(toolPath, toolColor, isOneShot ? 'one-shot' : 'tool');
      active.tool.push(toolPath);
      if (step.toolName) addToolLabel(svg, toolPath, step.toolName, isOneShot);
    }
  }

  if (isOneShot && step.oneShotNode) {
    addOneShotNodeBadge(svg, container, step.oneShotNode);
  }

  if (isValidation && step.validationNode) {
    addValidationNodeBadge(svg, container, step.validationNode);
  }

  if (isOneShot) {
    startOneShotRaf(active);
  } else {
    const delColor = isValidation ? VALIDATION_COLOR : DELEGATION_COLOR;
    const particleEntries = [
      ...active.delegation.map((path) => ({ path, color: delColor })),
      ...active.tool.map((path) => ({ path, color: TOOL_COLOR })),
    ];
    if (particleEntries.length) {
      spawnParticles(svg, particleEntries);
      startRaf(active);
    }
  }
}

export function prepareDiagram(container: HTMLElement): void {
  dimAllPaths(container);
  const svg = container.querySelector('svg');
  if (svg) clearParticleLayer(svg);
  stopRaf();
}

export function stopFlowAnimation(): void {
  stopRaf();
}

export async function runWorkflowSession(
  container: HTMLElement,
  steps: ArchWorkflowStep[],
  onStep: (step: ArchWorkflowStep, index: number) => void,
  stepMs = 2200
): Promise<void> {
  for (let i = 0; i < steps.length; i++) {
    applyWorkflowStep(container, steps[i]);
    onStep(steps[i], i);
    await new Promise((r) => setTimeout(r, stepMs));
  }
}

export function setupFlowAnimation(container: HTMLElement): () => void {
  prepareDiagram(container);
  return () => {
    stopFlowAnimation();
    const svg = container.querySelector('svg');
    if (svg) clearParticleLayer(svg);
    dimAllPaths(container);
  };
}
