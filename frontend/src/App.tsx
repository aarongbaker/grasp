import { Route, Routes } from 'react-router-dom';
import { AppShell } from './components/layout/AppShell';
import { LandingPage } from './pages/LandingPage';
import { LoginPage } from './pages/LoginPage';
import { RegisterPage } from './pages/RegisterPage';
import { DashboardPage } from './pages/DashboardPage';
import { NewSessionPage } from './pages/NewSessionPage';
import { SessionDetailPage } from './pages/SessionDetailPage';
import { ProfilePage } from './pages/ProfilePage';
import { AuthoredRecipeWorkspacePage } from './pages/AuthoredRecipeWorkspacePage';

export function App() {
  return (
    <Routes>
      <Route path="/welcome" element={<LandingPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route element={<AppShell />}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/sessions/new" element={<NewSessionPage />} />
        <Route path="/recipes/new" element={<AuthoredRecipeWorkspacePage />} />
        <Route path="/sessions/:sessionId" element={<SessionDetailPage />} />
        <Route path="/profile" element={<ProfilePage />} />
      </Route>
    </Routes>
  );
}
