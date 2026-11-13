import { Navigate, Route, Routes } from "react-router";
import { sortedRooms } from "../store/chatReducer";
import { useChatState } from "../store/chat";
import { RoomView } from "./RoomView";
import { Sidebar } from "./Sidebar";

function Home() {
  const state = useChatState();
  if (!state.ready) return <div className="empty">読み込み中…</div>;
  const first = sortedRooms(state)[0];
  if (first) return <Navigate to={`/rooms/${first.id}`} replace />;
  return <div className="empty">左の「＋」からチャンネルや DM を始めましょう</div>;
}

export function ChatLayout() {
  return (
    <div className="layout">
      <Sidebar />
      <main className="main">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/rooms/:roomId" element={<RoomView />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
