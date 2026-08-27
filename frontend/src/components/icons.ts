/**
 * 아이콘 어휘 한 곳 (lucide-react).
 *
 * 페이지는 `lucide-react` 를 직접 import 하지 않고 **여기서만** 가져온다. 이유는
 * 색 슬롯과 같다 — 같은 뜻에 같은 그림이어야 화면 사이를 옮겨 다닐 때 다시 배우지 않는다.
 * 새 아이콘이 필요하면 여기에 **이름을 붙여** 추가한다 (`Trash2` 가 아니라 `DeleteIcon`).
 *
 * 규칙:
 * - **아이콘은 라벨을 대신하지 않는다.** 버튼·네비 항목은 항상 글자를 함께 싣는다
 *   (색만으로 구분하지 않는다는 규칙과 같은 이유다 — 그림만으로도 구분하지 않는다).
 * - 글자가 옆에 있으면 그림은 장식이다 → `aria-hidden` 을 붙인다. lucide 는 기본으로
 *   `aria-hidden="true"` 를 내보내므로 대개 그냥 두면 된다.
 * - 아이콘만 있는 버튼(드로어 열기·닫기 등)에는 `aria-label` 을 반드시 붙인다.
 * - 크기는 `size-4`(본문·버튼) / `size-3.5`(배지·표 안) / `size-5`(네비·빈 상태 머리)
 *   세 가지만 쓴다.
 */

export type { LucideIcon } from 'lucide-react';

export {
  // ------------------------------------------------------------------ 네비게이션
  LayoutDashboard as DashboardIcon,
  ListFilter as PolicyIcon,
  Bug as ErrorGroupIcon,
  Settings as AdminIcon,
  Menu as MenuIcon,
  X as CloseIcon,
  ChevronRight as ChevronRightIcon,
  ArrowLeft as BackIcon,

  // ------------------------------------------------------ 관리 영역 하위 항목
  Cpu as LlmConnectionIcon,
  Database as LogSourceConnectionIcon,
  History as AnalysisJobIcon,
  Receipt as UsageIcon,
  Users as UsersIcon,

  // ------------------------------------------------------------------ 주 동작
  Play as RunIcon,
  Save as SaveIcon,
  Trash2 as DeleteIcon,
  Download as ExportIcon,
  Plus as AddIcon,
  RefreshCw as RefreshIcon,
  Search as SearchIcon,
  Pencil as EditIcon,
  Copy as CopyIcon,
  ExternalLink as ExternalLinkIcon,

  // ---------------------------------------------------------------- 상태·안내
  Info as InfoGlyph,
  TriangleAlert as WarningIcon,
  CircleCheck as SuccessIcon,
  CircleAlert as DangerIcon,
  Inbox as EmptyIcon,
  LogOut as LogoutIcon,

  // ------------------------------------------------------------------ stat 라벨
  Layers as GroupCountIcon,
  Clock as TimeIcon,
  Gauge as LimitIcon,
  Coins as CostIcon,
  FileText as ReportIcon,
  ShieldAlert as SeverityIcon,
  CalendarClock as ScheduleIcon,

  // -------------------------------------------------------------------- 테마
  Sun as ThemeLightIcon,
  Moon as ThemeDarkIcon,
  Monitor as ThemeSystemIcon,
} from 'lucide-react';
