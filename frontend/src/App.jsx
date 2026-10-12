import { lazy, Suspense } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AuthProvider } from './auth/AuthContext';
import { AdminRoute } from './auth/AdminRoute';
import { ProtectedRoute } from './auth/ProtectedRoute';
import { ChatWorkspace } from './components/ChatWorkspace';
import { FullPageLoader } from './components/FullPageLoader';
import { configurationError } from './lib/runtimeConfig';
import { LoginPage } from './pages/LoginPage';
import { RoleLandingPage } from './pages/RoleLandingPage';
import { SetPasswordPage } from './pages/SetPasswordPage';
import './App.css';

const AppointmentAdminPage = lazy(() =>
  import('./pages/AppointmentAdminPage').then((module) => ({
    default: module.AppointmentAdminPage,
  })),
);

function App() {
  if (configurationError) {
    return (
      <main className="status-screen">
        <span className="status-icon status-icon--error">!</span>
        <h1>Frontend configuration needed</h1>
        <p>{configurationError}</p>
        <small>Copy frontend/.env.example to frontend/.env and add your project values.</small>
      </main>
    );
  }

  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/set-password" element={<SetPasswordPage />} />
          <Route
            path="/"
            element={
              <ProtectedRoute>
                <RoleLandingPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/patient/chat"
            element={
              <ProtectedRoute>
                <ChatWorkspace role="patient" />
              </ProtectedRoute>
            }
          />
          <Route
            path="/admin/chat"
            element={
              <ProtectedRoute>
                <AdminRoute>
                  <ChatWorkspace role="admin" />
                </AdminRoute>
              </ProtectedRoute>
            }
          />
          <Route
            path="/admin/appointments"
            element={
              <ProtectedRoute>
                <AdminRoute>
                  <Suspense fallback={<FullPageLoader label="Opening appointment operations" />}>
                    <AppointmentAdminPage />
                  </Suspense>
                </AdminRoute>
              </ProtectedRoute>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}

export default App;
