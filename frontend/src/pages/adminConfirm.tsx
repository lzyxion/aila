/**
 * 관리 화면의 **되돌리기 어려운 동작** 하나를 감싸는 2단 버튼.
 *
 * 관리 영역에 모인 다섯 화면의 공통점은 "잘못 누르면 접근 권한이나 연결 설정이 움직인다"는
 * 것이다. 그런데 지금까지 위험 동작은 `variant="danger"` 로 **빨갛게만** 칠해져 있었고,
 * 한 번 누르면 그대로 실행됐다 — 계정 비활성화(그 계정의 세션이 전부 끊긴다)와 기본 연결
 * 지정(기존 기본 연결이 조용히 해제된다)이 "저장"과 같은 한 번 클릭이었다.
 *
 * 그래서 세 화면(계정·LLM 연결·Loki 연결)이 **같은 확인 절차**를 쓰게 이 한 곳에 둔다.
 * 화면마다 손으로 만들면 어떤 곳은 확인이 있고 어떤 곳은 없어진다.
 *
 * 규칙:
 * - **색만으로 위험을 알리지 않는다.** 무엇이 일어나는지(`question`)를 글자로 적고, 확정
 *   버튼의 라벨도 동작 이름을 그대로 쓴다("확인"이 아니라 "비활성화").
 * - 확인 단계에 들어가면 **확정 버튼으로 포커스를 옮긴다** — 키보드 사용자가 확인 문구를
 *   지나쳐 엉뚱한 곳에 있지 않도록.
 * - Escape 로 취소된다. 취소하면 포커스는 원래 버튼으로 돌아간다.
 * - 왜 위험한지는 `title` 이 아니라 `question` 에 적는다 (title 은 터치·키보드에서 안 뜬다).
 *
 * `components/**` 가 아니라 여기 있는 이유: 이번 phase 에서 공용 컴포넌트 계층은 기반
 * 트랙이 소유한다. 관리 화면 세 곳만 쓰는 동안은 이 자리가 맞고, 다른 트랙도 쓰게 되면
 * 그때 `components/ui.tsx` 로 올린다.
 */

import { useEffect, useRef, useState } from 'react';

import { Button, cx } from '../components/ui';

export function ConfirmButton({
  children,
  question,
  confirmLabel,
  variant = 'danger',
  size = 'sm',
  disabled,
  pending,
  pendingLabel = '처리 중…',
  className,
  onConfirm,
}: {
  /** 평상시 버튼 라벨. */
  children: React.ReactNode;
  /** 확인 단계에서 보여 줄 **결과** 한 줄. "정말?" 이 아니라 무엇이 일어나는지 적는다. */
  question: React.ReactNode;
  /** 확정 버튼 라벨. 없으면 평상시 라벨을 그대로 쓴다. */
  confirmLabel?: string;
  variant?: 'danger' | 'secondary' | 'primary';
  size?: 'sm' | 'md';
  disabled?: boolean;
  pending?: boolean;
  pendingLabel?: string;
  className?: string;
  onConfirm: () => void;
}) {
  const [armed, setArmed] = useState(false);
  /**
   * 포커스는 **감싸는 span** 을 통해 옮긴다. `Button` 은 공용 컴포넌트라(이번 phase 에서는
   * 읽기 전용) ref 를 받지 않으므로, 안쪽 `<button>` 을 DOM 에서 골라 부른다.
   */
  const boxRef = useRef<HTMLSpanElement>(null);
  /** 첫 렌더에서는 포커스를 옮기지 않는다 — 화면을 열자마자 위험 버튼이 잡히면 곤란하다. */
  const touched = useRef(false);

  useEffect(() => {
    if (!touched.current) {
      touched.current = true;
      return;
    }
    // 확인 단계에서는 확정 버튼(첫 번째)으로, 취소 후에는 원래 버튼(그때는 하나뿐이다)으로.
    boxRef.current?.querySelector('button')?.focus();
  }, [armed]);

  return (
    <span
      ref={boxRef}
      role={armed ? 'group' : undefined}
      aria-label={
        armed ? (typeof children === 'string' ? `${children} 확인` : '동작 확인') : undefined
      }
      className={cx(
        armed
          ? 'inline-flex flex-wrap items-center gap-1.5 rounded-lg border border-line-strong bg-surface-2 px-2 py-1.5'
          : 'contents',
        className,
      )}
      onKeyDown={(event) => {
        if (armed && event.key === 'Escape') {
          event.stopPropagation();
          setArmed(false);
        }
      }}
    >
      {armed ? (
        <>
          <span className="text-xs text-ink-soft">{question}</span>
          <Button
            size="sm"
            variant={variant}
            disabled={pending}
            onClick={() => {
              setArmed(false);
              onConfirm();
            }}
          >
            {pending ? pendingLabel : (confirmLabel ?? children)}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setArmed(false)}>
            취소
          </Button>
        </>
      ) : (
        <Button
          size={size}
          variant={variant}
          disabled={disabled || pending}
          onClick={() => setArmed(true)}
        >
          {pending ? pendingLabel : children}
        </Button>
      )}
    </span>
  );
}
