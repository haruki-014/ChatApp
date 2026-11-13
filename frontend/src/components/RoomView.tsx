import { useEffect } from "react";
import { Link, useParams } from "react-router";
import { useChatActions, useChatState } from "../store/chat";
import { Composer } from "./Composer";
import { MessageList } from "./MessageList";
import { RoomHeader } from "./RoomHeader";

export function RoomView() {
  const roomId = Number(useParams().roomId);
  const state = useChatState();
  const actions = useChatActions();
  const room = state.rooms[roomId];

  useEffect(() => {
    actions.setActive(roomId);
    return () => actions.setActive(null);
  }, [actions, roomId]);

  if (!state.ready) return <div className="empty">読み込み中…</div>;
  if (!room) {
    return (
      <div className="empty">
        このルームは見つからないか、参加していません。<Link to="/">ホームへ</Link>
      </div>
    );
  }
  return (
    <section className="room" key={room.id}>
      <RoomHeader room={room} />
      <MessageList room={room} />
      <Composer room={room} />
    </section>
  );
}
