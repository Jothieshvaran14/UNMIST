/**
 * Reusable UI primitives.
 *
 * Small, composable building blocks shared across screens so repeated patterns
 * (buttons, cards, status badges, loading/error/empty states, dialogs, toasts)
 * stay visually consistent without duplicating markup.
 */

import {
  createContext,
  forwardRef,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type CSSProperties,
  type ReactNode,
} from "react";
import {
  AlertCircle,
  CheckCircle2,
  Info,
  Inbox,
  RefreshCw,
  TriangleAlert,
  X,
} from "lucide-react";

/* ------------------------------------------------------------------ */
/* Button                                                              */
/* ------------------------------------------------------------------ */

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
type ButtonSize = "sm" | "md" | "lg";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  block?: boolean;
  loading?: boolean;
  icon?: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = "secondary",
    size = "md",
    block = false,
    loading = false,
    icon,
    className = "",
    children,
    disabled,
    ...rest
  },
  ref,
) {
  const classes = [
    "btn",
    `btn--${variant}`,
    size !== "md" ? `btn--${size}` : "",
    block ? "btn--block" : "",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <button ref={ref} className={classes} disabled={disabled || loading} {...rest}>
      {loading ? <span className="spinner" style={{ width: 14, height: 14 }} /> : icon}
      {children}
    </button>
  );
});

/* ------------------------------------------------------------------ */
/* Card                                                                */
/* ------------------------------------------------------------------ */

export function Card({
  title,
  icon,
  action,
  children,
  footer,
  pad = true,
  className = "",
  flat = false,
  headingLevel = 2,
}: {
  title?: ReactNode;
  icon?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  pad?: boolean;
  className?: string;
  flat?: boolean;
  headingLevel?: 2 | 3;
}) {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <section className={`card ${flat ? "card--flat" : ""} ${className}`.trim()}>
      {(title || action) && (
        <header className="card__head">
          <Heading className="card__title">
            {icon}
            {title}
          </Heading>
          {action}
        </header>
      )}
      <div className={pad ? "card__body" : "card__body--tight"}>{children}</div>
      {footer && <footer className="card__foot">{footer}</footer>}
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Badge                                                               */
/* ------------------------------------------------------------------ */

export type BadgeTone = "neutral" | "accent" | "success" | "warning" | "danger" | "info";

export function Badge({
  tone = "neutral",
  pulse = false,
  dot = false,
  children,
  title,
}: {
  tone?: BadgeTone;
  pulse?: boolean;
  dot?: boolean;
  children: ReactNode;
  title?: string;
}) {
  return (
    <span
      className={`badge badge--${tone} ${pulse ? "badge--spin" : ""}`.trim()}
      title={title}
    >
      {dot && <span className="badge__dot" aria-hidden="true" />}
      {children}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/* Feedback: alert / spinner / empty / error                           */
/* ------------------------------------------------------------------ */

export function Alert({
  tone = "info",
  title,
  children,
  action,
}: {
  tone?: "info" | "danger" | "warning" | "success";
  title?: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
}) {
  const Icon = tone === "danger" ? AlertCircle : tone === "warning" ? TriangleAlert : tone === "success" ? CheckCircle2 : Info;
  return (
    <div className={`alert alert--${tone}`} role={tone === "danger" ? "alert" : "status"}>
      <Icon aria-hidden="true" />
      <div className="alert__body">
        {title && <div className="alert__title">{title}</div>}
        {children}
      </div>
      {action && <div style={{ marginLeft: "auto" }}>{action}</div>}
    </div>
  );
}

export function Spinner({ large = false, label }: { large?: boolean; label?: string }) {
  return (
    <span className="row gap-3" role="status" aria-live="polite">
      <span className={`spinner ${large ? "spinner--lg" : ""}`} aria-hidden="true" />
      {label && <span className="text-sm text-muted">{label}</span>}
    </span>
  );
}

export function LoadingRow({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="loading-row">
      <span className="spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode;
  title: string;
  description?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="state-block">
      <div className="state-block__icon" aria-hidden="true">
        {icon ?? <Inbox />}
      </div>
      <div className="state-block__title">{title}</div>
      {description && <div className="state-block__desc">{description}</div>}
      {action && <div className="state-block__actions">{action}</div>}
    </div>
  );
}

/**
 * Renders a normalised API failure with a recovery action.
 * Only user-safe messages are shown — never stack traces or internals.
 */
export function ErrorState({
  error,
  onRetry,
  compact = false,
}: {
  error: unknown;
  onRetry?: () => void;
  compact?: boolean;
}) {
  const message = toUserMessage(error);
  if (compact) {
    return (
      <Alert
        tone="danger"
        title="Something went wrong"
        action={
          onRetry && (
            <Button size="sm" variant="secondary" onClick={onRetry} icon={<RefreshCw />}>
              Retry
            </Button>
          )
        }
      >
        {message}
      </Alert>
    );
  }
  return (
    <div className="state-block">
      <div className="state-block__icon state-block__icon--danger" aria-hidden="true">
        <AlertCircle />
      </div>
      <div className="state-block__title">Could not load this view</div>
      <div className="state-block__desc">{message}</div>
      {onRetry && (
        <div className="state-block__actions">
          <Button variant="primary" onClick={onRetry} icon={<RefreshCw />}>
            Try again
          </Button>
        </div>
      )}
    </div>
  );
}

/** Extract a safe message from any thrown value. */
export function toUserMessage(error: unknown): string {
  if (error instanceof Error && error.message) {
    // Defensive: never surface anything that smells like a stack trace.
    return error.message.split("\n")[0];
  }
  if (typeof error === "string" && error.trim()) return error;
  return "The request could not be completed.";
}

/* ------------------------------------------------------------------ */
/* Field                                                               */
/* ------------------------------------------------------------------ */

export function Field({
  label,
  hint,
  error,
  htmlFor,
  required,
  children,
}: {
  label: string;
  hint?: string;
  error?: string | null;
  htmlFor?: string;
  required?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="field">
      <label className="field__label" htmlFor={htmlFor}>
        {label}
        {required && (
          <span aria-hidden="true" style={{ color: "var(--danger)" }}>
            {" *"}
          </span>
        )}
      </label>
      {children}
      {error ? (
        <span className="field__error" role="alert">
          {error}
        </span>
      ) : (
        hint && <span className="field__hint">{hint}</span>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Modal                                                               */
/* ------------------------------------------------------------------ */

export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  labelledBy,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  labelledBy?: string;
}) {
  const titleId = useId();
  const ref = useRef<HTMLDivElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return undefined;
    previouslyFocused.current = document.activeElement as HTMLElement | null;

    const node = ref.current;
    const focusable = node?.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    );
    focusable?.[0]?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !node) return;
      const items = Array.from(
        node.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((el) => !el.hasAttribute("disabled"));
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown, true);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      document.body.style.overflow = "";
      previouslyFocused.current?.focus?.();
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy ?? titleId}
        ref={ref}
      >
        <div className="modal__head">
          <div>
            <h2 className="modal__title" id={titleId}>
              {title}
            </h2>
            {description && <p className="modal__desc">{description}</p>}
          </div>
          <Button variant="ghost" size="sm" className="btn--icon" onClick={onClose} aria-label="Close dialog">
            <X />
          </Button>
        </div>
        {children && <div className="modal__body">{children}</div>}
        {footer && <div className="modal__foot">{footer}</div>}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Toasts                                                              */
/* ------------------------------------------------------------------ */

type ToastTone = "info" | "success" | "danger" | "warning";
interface ToastItem {
  id: number;
  tone: ToastTone;
  title: string;
  message?: string;
}

interface ToastApi {
  push: (input: { tone?: ToastTone; title: string; message?: string }) => void;
  success: (title: string, message?: string) => void;
  error: (title: string, message?: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(1);

  const remove = useCallback((id: number) => {
    setItems((prev) => prev.filter((item) => item.id !== id));
  }, []);

  const push = useCallback(
    (input: { tone?: ToastTone; title: string; message?: string }) => {
      const id = nextId.current++;
      setItems((prev) => [...prev, { id, tone: input.tone ?? "info", ...input }]);
      window.setTimeout(() => remove(id), 5200);
    },
    [remove],
  );

  const api = useMemo<ToastApi>(
    () => ({
      push,
      success: (title, message) => push({ tone: "success", title, ...(message ? { message } : {}) }),
      error: (title, message) => push({ tone: "danger", title, ...(message ? { message } : {}) }),
    }),
    [push],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toast-region" aria-live="polite" aria-atomic="false">
        {items.map((item) => (
          <ToastCard key={item.id} item={item} onDismiss={() => remove(item.id)} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

function ToastCard({ item, onDismiss }: { item: ToastItem; onDismiss: () => void }) {
  const Icon =
    item.tone === "success"
      ? CheckCircle2
      : item.tone === "danger"
        ? AlertCircle
        : item.tone === "warning"
          ? TriangleAlert
          : Info;
  return (
    <div className={`toast toast--${item.tone}`}>
      <Icon aria-hidden="true" />
      <div className="toast__body">
        <div className="toast__title">{item.title}</div>
        {item.message && <div className="toast__msg">{item.message}</div>}
      </div>
      <button
        className="btn btn--ghost btn--sm btn--icon"
        onClick={onDismiss}
        aria-label="Dismiss notification"
      >
        <X />
      </button>
    </div>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside <ToastProvider>");
  return ctx;
}

/* ------------------------------------------------------------------ */
/* Misc helpers                                                        */
/* ------------------------------------------------------------------ */

export function ProgressBar({ value, label }: { value: number; label?: string }) {
  const clamped = Math.max(0, Math.min(100, value));
  return (
    <div
      className="progress"
      role="progressbar"
      aria-valuenow={clamped}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label ?? "Upload progress"}
    >
      <div className="progress__bar" style={{ width: `${clamped}%` }} />
    </div>
  );
}

export function SkeletonRows({ rows = 4 }: { rows?: number }) {
  return (
    <div aria-hidden="true">
      {Array.from({ length: rows }).map((_, index) => (
        <div className="skel-row" key={index}>
          <div className="skel" style={{ height: 13, width: "42%" }} />
          <div className="skel" style={{ height: 11, width: "68%" }} />
        </div>
      ))}
    </div>
  );
}

export function Stat({
  label,
  value,
  note,
  icon,
  style,
}: {
  label: string;
  value: ReactNode;
  note?: ReactNode;
  icon?: ReactNode;
  style?: CSSProperties;
}) {
  return (
    <div className="stat" style={style}>
      <div className="stat__label">
        {icon}
        {label}
      </div>
      <div className="stat__value">{value}</div>
      {note && <div className="stat__note">{note}</div>}
    </div>
  );
}
