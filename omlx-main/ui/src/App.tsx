import { Route, Routes } from 'react-router-dom';
import AppShell from './app/AppShell';
import WorkspaceList from './pages/WorkspaceList';
import WorkspaceDetail from './pages/WorkspaceDetail';
import WorkspaceLineagePage from './pages/WorkspaceLineagePage';
import WorkspaceDiffPage from './pages/WorkspaceDiffPage';
import ForkPage from './pages/ForkPage';
import DiffPage from './pages/DiffPage';
import Transfers from './pages/Transfers';
import Maintenance from './pages/Maintenance';
import SettingsPage from './pages/SettingsPage';
import ModelsPage from './pages/ModelsPage';
import ChatPage from './pages/ChatPage';
import HelpPage from './pages/HelpPage';

export default function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<WorkspaceList />} />
        <Route path="/w/:model/:session" element={<WorkspaceDetail />} />
        <Route path="/w/:model/:session/lineage" element={<WorkspaceLineagePage />} />
        <Route path="/w/:model/:session/diff" element={<WorkspaceDiffPage />} />
        <Route path="/w/:model/:session/fork" element={<ForkPage />} />
        <Route path="/diff" element={<DiffPage />} />
        <Route path="/transfers" element={<Transfers />} />
        <Route path="/maintenance" element={<Maintenance />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/models" element={<ModelsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/help" element={<HelpPage />} />
      </Routes>
    </AppShell>
  );
}
