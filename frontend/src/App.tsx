import type { ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router";
import { ChatLayout } from "./components/ChatLayout";
import { AuthPage } from "./pages/AuthPage";
import { AuthProvider, useAuth } from "./store/auth";
import { ChatProvider } from "./store/chat";

function RequireAuth({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  if (status === "loading") return <div className="splash">読み込み中…</div>;
  if (status === "anonymous") return <Navigate to="/login" replace />;
  return children;
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<AuthPage mode="login" />} />
        <Route path="/signup" element={<AuthPage mode="signup" />} />
        <Route
          path="/*"
          element={
            <RequireAuth>
              <ChatProvider>
                <ChatLayout />
              </ChatProvider>
            </RequireAuth>
          }
        />
      </Routes>
    </AuthProvider>
  );
}
