import AnimatedArchitecture from '@/components/architecture/AnimatedArchitectureLoader';

export default function ArchitecturePage() {
  return (
    <div className="max-w-6xl">
      <header className="mb-6">
        <p className="text-xs font-semibold uppercase tracking-widest text-dna-500">
          Architecture
        </p>
        <h1 className="mt-1 text-2xl font-bold text-slate-900 dark:text-white">
          Multi-agent system
        </h1>
      </header>
      <AnimatedArchitecture />
    </div>
  );
}
