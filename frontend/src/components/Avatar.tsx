import type { UserSummary } from "../api/types";

const COLORS = ["#e01e5a", "#2eb67d", "#ecb22e", "#36c5f0", "#4a154b", "#1264a3", "#e8912d", "#7c3085"];

export function Avatar({ user, size = 36 }: { user: UserSummary; size?: number }) {
  const style = { width: size, height: size, fontSize: size * 0.45 };
  if (user.avatar_url) {
    return <img className="avatar" src={user.avatar_url} alt="" style={style} />;
  }
  return (
    <span className="avatar avatar-fallback" style={{ ...style, background: COLORS[user.id % COLORS.length] }}>
      {(user.display_name || user.username).slice(0, 1).toUpperCase()}
    </span>
  );
}

export function PresenceDot({ online }: { online: boolean }) {
  return <span className={`presence ${online ? "online" : "offline"}`} title={online ? "オンライン" : "オフライン"} />;
}
