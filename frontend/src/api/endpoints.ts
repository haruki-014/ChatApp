import { request } from "./client";
import type {
  AttachmentPresign,
  AvatarPresign,
  Channel,
  Me,
  Member,
  Message,
  MessagePage,
  ReadUpdatedData,
  Room,
  RoomsResponse,
  SyncResponse,
  UserSummary,
} from "./types";

export const Api = {
  // 認証
  csrf: () => request<void>("/api/auth/csrf"),
  me: () => request<Me>("/api/auth/me"),
  login: (email: string, password: string) =>
    request<Me>("/api/auth/login", { method: "POST", body: { email, password } }),
  signup: (body: { email: string; username: string; password: string; display_name: string }) =>
    request<Me>("/api/auth/signup", { method: "POST", body }),
  logout: () => request<void>("/api/auth/logout", { method: "POST" }),

  // ユーザー・プレゼンス
  searchUsers: (q: string) => request<UserSummary[]>("/api/users", { query: { q, limit: 20 } }),
  updateProfile: (body: Partial<Pick<Me, "display_name" | "status_emoji" | "status_text">> & { avatar_key?: string }) =>
    request<Me>("/api/users/me", { method: "PATCH", body }),
  presignAvatar: (content_type: string, size: number) =>
    request<AvatarPresign>("/api/users/me/avatar/presign", { method: "POST", body: { content_type, size } }),
  presence: () => request<{ online_user_ids: number[] }>("/api/presence"),

  // ルーム
  rooms: () => request<RoomsResponse>("/api/rooms"),
  createRoom: (body: { type: "group" | "channel"; name?: string; is_private?: boolean; member_ids: number[] }) =>
    request<Room>("/api/rooms", { method: "POST", body }),
  openDm: (user_id: number) => request<Room>("/api/rooms/dm", { method: "POST", body: { user_id } }),
  channels: () => request<Channel[]>("/api/channels"),
  joinRoom: (id: number) => request<Room>(`/api/rooms/${id}/join`, { method: "POST" }),
  leaveRoom: (id: number) => request<void>(`/api/rooms/${id}/leave`, { method: "POST" }),
  renameRoom: (id: number, name: string) => request<Room>(`/api/rooms/${id}`, { method: "PATCH", body: { name } }),
  members: (id: number) => request<Member[]>(`/api/rooms/${id}/members`),
  addMembers: (id: number, user_ids: number[]) =>
    request<UserSummary[]>(`/api/rooms/${id}/members`, { method: "POST", body: { user_ids } }),
  removeMember: (id: number, userId: number) =>
    request<void>(`/api/rooms/${id}/members/${userId}`, { method: "DELETE" }),
  setMuted: (id: number, is_muted: boolean) =>
    request<Room>(`/api/rooms/${id}/membership`, { method: "PATCH", body: { is_muted } }),
  markRead: (id: number, last_read_message_id: number) =>
    request<ReadUpdatedData>(`/api/rooms/${id}/read`, { method: "POST", body: { last_read_message_id } }),

  // メッセージ
  messages: (roomId: number, params: { before?: number; after?: number; limit?: number } = {}) =>
    request<MessagePage>(`/api/rooms/${roomId}/messages`, { query: params }),
  postMessage: (roomId: number, body: { body: string; attachment_ids: number[]; client_msg_id: string }) =>
    request<Message>(`/api/rooms/${roomId}/messages`, { method: "POST", body }),
  editMessage: (id: number, body: string) => request<Message>(`/api/messages/${id}`, { method: "PATCH", body: { body } }),
  deleteMessage: (id: number) => request<void>(`/api/messages/${id}`, { method: "DELETE" }),
  sync: (since: string) => request<SyncResponse>("/api/sync", { query: { since } }),

  // ファイル
  presignAttachment: (roomId: number, body: { filename: string; content_type: string; size: number }) =>
    request<AttachmentPresign>(`/api/rooms/${roomId}/attachments/presign`, { method: "POST", body }),
};
