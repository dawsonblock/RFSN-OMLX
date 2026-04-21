import { useState, type ReactNode } from 'react';
import SidebarNav from './SidebarNav';
import TopBar from './TopBar';
import DiagnosticsPanel from './DiagnosticsPanel';

export default function AppShell({ children }: { children: ReactNode }) {
  const [diagOpen, setDiagOpen] = useState(false);
  return (
    <div className="flex h-screen flex-col">
      <TopBar onToggleDiagnostics={() => setDiagOpen((v) => !v)} />
      <div className="flex flex-1 overflow-hidden">
        <aside className="w-48 shrink-0 border-r border-neutral-200 bg-white">
          <SidebarNav />
        </aside>
        <main className="flex-1 overflow-y-auto bg-neutral-50 p-4">
          <div className="mx-auto max-w-6xl">{children}</div>
        </main>
        {diagOpen && <DiagnosticsPanel />}
      </div>
    </div>
  );
}
