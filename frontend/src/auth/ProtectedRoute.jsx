import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from './authState';
import { FullPageLoader } from '../components/FullPageLoader';

export function ProtectedRoute({ children }) {
  const { session, isLoading } = useAuth();
  const location = useLocation();

  if (isLoading) return <FullPageLoader label="Restoring your secure session" />;
  if (!session) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return children;
}
