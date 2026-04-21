import { Link, NavLink, Route, Routes } from 'react-router-dom';
import WorkspaceList from './pages/WorkspaceList';
import WorkspaceDetail from './pages/WorkspaceDetail';
import ForkPage from './pages/ForkPage';
import DiffPage from './pages/DiffPage';
import Transfers from './pages/Transfers';
import Maintenance from './pages/Maintenance';
import SettingsPage from './pages/SettingsPage';

function Nav() {
  const cls = ({ isActive }: { isActive: boolean }) =>
    `px-3 py-1.5 rounded text-sm font-medium ${
      isActive ? 'bg-blue-600 text-white' : 'text-neutral-700 hover:bg-neutral-200'
    }`;
  return (
    <nav className="flex items-center gap-2 border-b border-neutral-200 bg-white px-4 py-2">
      <Link to="/" className="text-base font-semibold text-neutral-900">
        OMLX · Operator
      </Link>
      <div className="ml-6 flex gap-1">
        <NavLink to="/" end className={cls}>
          Workspaces
        </NavLink>
        <NavLink to="/diff" className={cls}>
          Diff
        </NavLink>
        <NavLink to="/transfers" className={cls}>
          Transfers
        </NavLink>
        <NavLink to="/maintenance" className={cls}>
          Maintenance
        </NavLink>
        <NavLink to="/settings" className={cls}>
          Settings
        </NavLink>
      </div>
    </nav>
  );
}

export default function App() {
  return (
    <div className="min-h-screen">
      <Nav />
      <main className="mx-auto max-w-7xl p-4">
        <Routes>
          <Route path="/" element={<WorkspaceList />} />
          <Route path="/w/:model/:session" element={<WorkspaceDetail />} />
          <Route path="/w/:model/:session/fork" element={<ForkPage />} />
          <Route path="/diff" element={<DiffPage />} />
          <Route path="/transfers" element={<Transfers />} />
          <Route path="/maintenance" element={<Maintenance />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
    </div>
  );
}
