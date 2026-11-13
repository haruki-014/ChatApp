// REST と WebSocket で共通の型（docs/03-rest-api.md の 3-7、docs/04-websocket-events.md の 4-5）

export type UserSummary = {
  id: number;
  username: string;
  display_name: string;
  avatar_url: string | null;
  status_emoji: string;
  status_text: string;
  is_online: boolean;
};

export type Me = UserSummary & { email: string };

export type Attachment = {
  id: number;
  original_filename: string;
  content_type: string;
  size: number;
  is_image: boolean;
  download_url: string;
  preview_url: string | null;
};

export type Message = {
  id: number;
  room_id: number;
  sender: UserSummary;
  body: string;
  attachments: Attachment[];
  client_msg_id: string | null;
  created_at: string;
  updated_at: string;
  edited_at: string | null;
  deleted_at: string | null;
};

export type RoomType = "dm" | "group" | "channel";
export type Role = "owner" | "admin" | "member";

export type Room = {
  id: number;
  type: RoomType;
  name: string | null;
  display_name: string;
  is_private: boolean;
  dm_partner: UserSummary | null;
  member_count: number;
  last_message_at: string | null;
  role: Role;
  unread_count: number;
  last_read_message_id: number;
  is_muted: boolean;
};

export type Channel = { id: number; name: string; member_count: number; is_member: boolean; created_at: string };
export type Member = { user: UserSummary; role: Role; joined_at: string };

export type MessagePage = {
  results: Message[];
  has_more: boolean;
  next_before?: number | null;
  next_after?: number;
};

export type RoomsResponse = { rooms: Room[]; sync_token: string };
export type SyncResponse = { sync_token: string; reset: boolean; rooms: Room[]; messages: Message[] };

export type PresignedUpload = { url: string; fields: Record<string, string> };
export type AttachmentPresign = { attachment_id: number; upload: PresignedUpload; expires_in: number };
export type AvatarPresign = { avatar_key: string; upload: PresignedUpload; expires_in: number };

// ---------------------------------------------------------------------------
// WebSocket イベント（サーバー → クライアント）。type で判別できる union 型にしておくと、
// switch (event.type) の各 case で data の型が自動的に絞り込まれる
// ---------------------------------------------------------------------------
type Envelope<T extends string, D> = { type: T; ts: string; data: D };

export type MessageDeletedData = { id: number; room_id: number; deleted_at: string; updated_at: string };
export type ReadUpdatedData = { room_id: number; last_read_message_id: number; unread_count: number };

export type WsEvent =
  | Envelope<"hello", { user_id: number }>
  | Envelope<"pong", Record<string, never>>
  | Envelope<"message.created", Message>
  | Envelope<"message.updated", Message>
  | Envelope<"message.deleted", MessageDeletedData>
  | Envelope<"read.updated", ReadUpdatedData>
  | Envelope<"room.added", Room>
  | Envelope<"room.removed", { room_id: number }>
  | Envelope<"room.updated", { id: number; name: string | null; display_name: string; is_private: boolean }>
  | Envelope<"room.member_joined", { room_id: number; user: UserSummary; member_count: number }>
  | Envelope<"room.member_left", { room_id: number; user_id: number; member_count: number }>
  | Envelope<"presence.changed", { user_id: number; is_online: boolean }>
  | Envelope<"user.updated", { user: UserSummary }>;
