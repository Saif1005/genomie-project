import { toCanvas } from 'html-to-image';
import { GIFEncoder, quantize, applyPalette } from 'gifenc';
import type { ArchWorkflowStep } from '@/lib/mermaid/animatedArchitectureDiagram';
import { applyWorkflowStep, prepareDiagram } from '@/lib/mermaid/flowAnimation';

export type ExportFormat = 'webm' | 'gif';

export type CaptureSessionOptions = {
  format: ExportFormat;
  fps?: number;
  stepMs?: number;
  captureTarget: HTMLElement;
  diagramContainer: HTMLElement;
  steps: ArchWorkflowStep[];
  onStep?: (step: ArchWorkflowStep, index: number) => void;
  onProgress?: (message: string) => void;
};

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

async function captureFrame(target: HTMLElement): Promise<HTMLCanvasElement> {
  return toCanvas(target, {
    pixelRatio: 1,
    backgroundColor: '#0f172a',
    cacheBust: true,
  });
}

async function collectFrames(
  target: HTMLElement,
  durationMs: number,
  fps: number
): Promise<HTMLCanvasElement[]> {
  const interval = 1000 / fps;
  const frames: HTMLCanvasElement[] = [];
  const end = Date.now() + durationMs;

  while (Date.now() < end) {
    frames.push(await captureFrame(target));
    const remaining = end - Date.now();
    if (remaining > 0) {
      await new Promise((r) => setTimeout(r, Math.min(interval, remaining)));
    }
  }

  return frames;
}

async function framesToWebm(frames: HTMLCanvasElement[], fps: number): Promise<Blob> {
  if (!frames.length) throw new Error('No frame captured');

  const w = frames[0].width;
  const h = frames[0].height;
  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('Canvas 2D indisponible');

  const stream = canvas.captureStream(fps);
  const mimeType = MediaRecorder.isTypeSupported('video/webm;codecs=vp9')
    ? 'video/webm;codecs=vp9'
    : 'video/webm';
  const recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 4_000_000 });
  const chunks: Blob[] = [];

  recorder.ondataavailable = (e) => {
    if (e.data.size) chunks.push(e.data);
  };

  const done = new Promise<Blob>((resolve, reject) => {
    recorder.onstop = () => resolve(new Blob(chunks, { type: mimeType }));
    recorder.onerror = () => reject(new Error('MediaRecorder failed'));
  });

  recorder.start();
  const frameDelay = 1000 / fps;

  for (const frame of frames) {
    ctx.drawImage(frame, 0, 0, w, h);
    await new Promise((r) => setTimeout(r, frameDelay));
  }

  recorder.stop();
  return done;
}

function framesToGif(frames: HTMLCanvasElement[], fps: number): Blob {
  if (!frames.length) throw new Error('No frame captured');

  const w = frames[0].width;
  const h = frames[0].height;
  const delayCs = Math.round(100 / fps);
  const gif = GIFEncoder();

  for (const frame of frames) {
    const ctx = frame.getContext('2d');
    if (!ctx) continue;
    const { data } = ctx.getImageData(0, 0, w, h);
    const palette = quantize(data, 256);
    const index = applyPalette(data, palette);
    gif.writeFrame(index, w, h, { palette, delay: delayCs });
  }

  gif.finish();
  const bytes = gif.bytes();
  return new Blob([new Uint8Array(bytes)], { type: 'image/gif' });
}

/** Runs the workflow animation and exports it as WebM or GIF */
export async function runAndExportSession(options: CaptureSessionOptions): Promise<void> {
  const {
    format,
    fps = 10,
    stepMs = 2200,
    captureTarget,
    diagramContainer,
    steps,
    onStep,
    onProgress,
  } = options;

  onProgress?.('Preparing the recording…');
  prepareDiagram(diagramContainer);

  const allFrames: HTMLCanvasElement[] = [];

  for (let i = 0; i < steps.length; i++) {
    applyWorkflowStep(diagramContainer, steps[i]);
    onStep?.(steps[i], i);
    onProgress?.(`Capturing step ${i + 1}/${steps.length}…`);
    const stepFrames = await collectFrames(captureTarget, stepMs, fps);
    allFrames.push(...stepFrames);
  }

  onProgress?.(format === 'gif' ? 'Encoding GIF…' : 'Encoding WebM video…');

  const timestamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');

  if (format === 'gif') {
    downloadBlob(framesToGif(allFrames, fps), `architecture-harness-${timestamp}.gif`);
  } else {
    downloadBlob(
      await framesToWebm(allFrames, fps),
      `architecture-harness-${timestamp}.webm`
    );
  }

  onProgress?.('Export finished.');
}
