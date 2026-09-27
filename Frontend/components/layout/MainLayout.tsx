'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import useSWR from 'swr';
import { Cpu, Dna, Wifi, WifiOff } from 'lucide-react';
import ThemeToggle from '@/components/layout/ThemeToggle';
import { checkHealth } from '@/lib/api';

interface MainLayoutProps {
  children: React.ReactNode;
}

const NAV = [
  { href: '/', label: 'Analyze' },
  { href: '/cohort', label: 'Cohort' },
  { href: '/benchmarks', label: 'Benchmarks' },
  { href: '/architecture', label: 'Architecture' },
];

export default function MainLayout({ children }: MainLayoutProps) {
  const pathname = usePathname();
  const { data: health, error, isLoading } = useSWR('health', checkHealth, {
    refreshInterval: 15_000,
    revalidateOnFocus: true,
  });
  const connected = !error && health?.status === 'ok';

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 transition-colors dark:bg-slate-950 dark:text-slate-100">
      <div
        className="pointer-events-none fixed inset-0 opacity-[0.35] dark:opacity-[0.12]"
        style={{
          backgroundImage:
            'radial-gradient(circle at 20% 20%, rgba(6,182,212,0.15) 0%, transparent 50%), radial-gradient(circle at 80% 0%, rgba(14,165,233,0.12) 0%, transparent 40%)',
        }}
      />

      <header className="sticky top-0 z-50 border-b border-slate-200/80 bg-white/80 backdrop-blur-md dark:border-slate-800/80 dark:bg-slate-950/80">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <div className="flex items-center gap-6">
            <Link href="/" className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-dna-500 to-sky-600 text-white shadow-lg shadow-dna-500/25">
                <Dna className="h-5 w-5" />
              </div>
              <div>
                <p className="text-sm font-bold leading-tight tracking-tight">GermlineIQ</p>
                <p className="text-[11px] leading-tight text-slate-500 dark:text-slate-400">Hereditary cancer germline analysis</p>
              </div>
            </Link>
            <nav className="hidden items-center gap-1 md:flex" aria-label="Main navigation">
              {NAV.map((n) => {
                const active = n.href === '/' ? pathname === '/' : pathname?.startsWith(n.href);
                return (
                  <Link
                    key={n.href}
                    href={n.href}
                    className={`rounded-lg px-3 py-1.5 text-sm font-medium transition ${
                      active
                        ? 'bg-dna-500/10 text-dna-700 dark:text-dna-300'
                        : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-white'
                    }`}
                  >
                    {n.label}
                  </Link>
                );
              })}
            </nav>
          </div>

          <div className="flex items-center gap-2">
            <div
              className={`hidden items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-medium sm:flex ${
                connected
                  ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400'
                  : 'border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400'
              }`}
            >
              {connected ? <Wifi className="h-3.5 w-3.5" /> : <WifiOff className="h-3.5 w-3.5" />}
              <span>{isLoading ? 'Connecting…' : connected ? 'API online' : 'API offline'}</span>
            </div>
            {health?.pipeline_backend && (
              <div
                className="hidden items-center gap-1.5 rounded-full border border-slate-200 bg-slate-100 px-3 py-1.5 text-xs text-slate-600 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 lg:flex"
                title={health.pipeline_backend_reason}
              >
                <Cpu className="h-3.5 w-3.5 text-dna-500" />
                <span className="max-w-[220px] truncate">
                  {health.pipeline_backend === 'parabricks' ? 'Parabricks GPU' : 'GATK4 CPU'} · {health.orchestrator}
                </span>
              </div>
            )}
            <ThemeToggle />
          </div>
        </div>
        <nav className="flex gap-1 overflow-x-auto border-t border-slate-200/60 px-4 py-1.5 md:hidden dark:border-slate-800/60" aria-label="Main navigation">
          {NAV.map((n) => (
            <Link key={n.href} href={n.href} className="rounded-md px-2.5 py-1 text-xs font-medium text-slate-600 dark:text-slate-300">
              {n.label}
            </Link>
          ))}
        </nav>
      </header>

      <main className="relative mx-auto max-w-7xl px-4 py-8 sm:px-6 sm:py-10">{children}</main>

      <footer className="relative border-t border-slate-200 py-4 text-center text-xs text-slate-500 dark:border-slate-800 dark:text-slate-500">
        GermlineIQ · GATK · ClinVar · deterministic risk rules · verified BioGPT commentary · for clinical research use only
      </footer>
    </div>
  );
}
