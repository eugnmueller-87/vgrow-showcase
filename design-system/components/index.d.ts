// VGrow Cockpit — window.VGrowCockpit. Vanilla element factories; each returns a DOM element styled by bundle.css + tokens.css.
// A consumer calls VGrowCockpit.StatusChip({ state: 'warn', text: 'Needs you' }) and appends the result.

export type StatusState = 'ok' | 'warn' | 'info' | 'bad' | 'demo';

export interface ButtonProps {
  /** Sentence-case verb phrase: "Save the pot". */
  label: string;
  /** primary: one per view · soft: a step Hank asks for · arm: stops or overrides, last in its card. */
  variant?: 'primary' | 'soft' | 'arm';
  /** 36px, 13px text: only inside dense rows. */
  small?: boolean;
  disabled?: boolean;
  /** Full opacity, dashed border, muted text; the handler is not attached. Put a reason line beside it. */
  locked?: boolean;
  type?: 'button' | 'submit';
  onClick?: (e: MouseEvent) => void;
}

export interface InputProps {
  /** small-strong label; put the unit here ("Soil in the pot in litres"). */
  label: string;
  /** small muted hint under the field; turns bad when invalid. */
  hint?: string;
  invalid?: boolean;
  /** Any other key becomes an attribute on the <input>: type, placeholder, value, inputmode, min, max, step, name. */
  [attr: string]: string | number | boolean | undefined;
}

export interface StatusChipProps {
  /** Omit for the neutral chip (Off, a count). */
  state?: StatusState;
  /** The word is the status: "Monitoring", "Paused until 9:37 AM". */
  text: string;
  /** On side (the sidebar, the phone top bar): side-fill with dot-ok / dot-bad / dot-demo. */
  onSide?: boolean;
}

export interface BannerProps {
  variant?: 'warn' | 'bad' | 'info' | 'ok';
  /** Two or three words naming the situation; the colon is added. */
  lead?: string;
  text: string;
  /** One small button that resolves the banner. */
  action?: ButtonProps;
}

export interface SegmentedControlProps {
  /** aria-label of the group. */
  label?: string;
  options: Array<{ value: string; label: string; /** bad at rest, bad-fill when chosen: the Off mode while the autopilot runs. */ arm?: boolean }>;
  value: string;
  /** Stretch to the container. */
  block?: boolean;
  /** Low-stakes switch: chosen segment is card with shadow-segment instead of accent-strong. */
  quiet?: boolean;
  locked?: boolean;
  onChange?: (value: string) => void;
}

export interface DisclosureProps {
  title: string;
  /** One line naming what is inside. */
  description?: string;
  /** Shown at the right; a warn or bad chip should come with open: true. */
  chip?: StatusChipProps;
  open?: boolean;
  body?: Node | string | Array<Node | string>;
}

export interface CardProps {
  title: string;
  /** The page's one hero card: title in heading (20px). */
  hero?: boolean;
  link?: { label: string; href: string };
  body?: Node | string | Array<Node | string>;
}

export interface DeviceCardProps {
  name: string;
  state?: StatusState;
  stateText: string;
  /** The device's own unit as reported: "0%", "Off", 3. Missing shows an en dash. */
  reading?: string | number;
  /** Why, in one sentence. Never repeats the state. */
  reason?: string;
  /** The rule or limits line, in ink. */
  rule?: string;
  action?: ButtonProps;
  /** One row: name, chip, reading. For monitoring, off and paused devices with nothing to say. */
  compact?: boolean;
  /** warn border; defaults to state === 'warn'. */
  attention?: boolean;
}

export interface TimelineEntry {
  /** step: autopilot acted · suggest: waiting for you · manual: you acted · hold: autopilot holds back · failed: a fail-safe or error. */
  kind?: 'step' | 'suggest' | 'manual' | 'hold' | 'failed';
  time: string;
  text: string;
  /** A grouped repeat: shows "4×" as a chip after the text. */
  count?: number;
}
export interface TimelineProps { entries: TimelineEntry[] }

export interface SidebarNavProps {
  brand: { name: string; sub?: string; markSrc?: string; href?: string };
  groups: Array<{ caption: string; items: Array<{ label: string; href?: string; active?: boolean; /** Inline SVG text of a 24px/1.6 icon. */ iconSvg?: string; plain?: boolean }> }>;
  foot?: Array<Node | string>;
}

export interface TopbarProps {
  title: string;
  subtitle?: string;
  health?: { state: StatusState; text: string; /** "Readings 51 s ago · Device status 8 min ago" */ age?: string };
  tools?: Node[];
}

export interface TileProps {
  key: string;
  source?: string;
  value: string | number;
  unit?: string;
  state?: StatusState;
  stateText?: string;
  series?: 'temp' | 'hum' | 'vpd' | 'water';
  /** The last hours of the series; drawn as the sparkline along the bottom edge. */
  points?: number[];
  stale?: boolean;
  alarm?: boolean;
}

export interface ToggleProps { label: string; pressed?: boolean; /** bad-fill: takes over or stops. */ arm?: boolean; locked?: boolean; disabled?: boolean; onChange?: (pressed: boolean) => void }
export interface RangeProps { id: string; label: string; min: number; max: number; step?: number; value: number; format?: (v: number | string) => string; locked?: boolean; onInput?: (v: number) => void }
export interface StepperProps { label?: string; level?: number; max?: number; onChange?: (level: number) => void }
export interface ChipsProps { label?: string; options: Array<{ value: string; label: string; locked?: boolean }>; chosen?: string[]; onChange?: (chosen: string[]) => void }
export interface LadderProps { levels: Array<{ title: string; text: string }>; current: 0 | 1 | 2 }
export interface RulesListProps { rules: Array<{ text: string; detail?: string; /** detail in warn: a missing precondition. */ need?: boolean; pressed?: boolean; onChange?: (pressed: boolean) => void }> }
export interface PortsListProps { ports: Array<{ name: string; detail?: string; control?: Node }> }
export interface GaugeProps { label: string; value?: number | string; unit?: string; min: number; max: number; band?: [number, number]; tick?: number; state?: 'high' | 'low' | 'none'; targetText?: string }
export interface EmptyStateProps { title: string; text: string; action?: ButtonProps }
export interface PageTabsProps { label?: string; tabs: Array<{ label: string; href?: string; active?: boolean }> }
export interface StatuslineProps { text: string; state?: StatusState; stateText?: string; href?: string; /** Inline SVG of the 22×40 plant art. */ artSvg?: string }

export interface VGrowCockpitNamespace {
  version: 1;
  icon(name: 'info' | 'warn' | 'bad' | 'ok' | 'chevron', strokeWidth?: number): SVGSVGElement;
  Button(props: ButtonProps): HTMLButtonElement;
  Input(props: InputProps): HTMLLabelElement;
  StatusChip(props: StatusChipProps): HTMLSpanElement;
  Banner(props: BannerProps): HTMLDivElement;
  SegmentedControl(props: SegmentedControlProps): HTMLDivElement;
  Disclosure(props: DisclosureProps): HTMLDetailsElement;
  Card(props: CardProps): HTMLElement;
  DeviceCard(props: DeviceCardProps): HTMLElement;
  Timeline(props: TimelineProps): HTMLOListElement;
  SidebarNav(props: SidebarNavProps): HTMLElement;
  Topbar(props: TopbarProps): HTMLElement;
  Tile(props: TileProps): HTMLDivElement;
  Toggle(props: ToggleProps): HTMLButtonElement;
  Range(props: RangeProps): HTMLDivElement;
  Stepper(props: StepperProps): HTMLDivElement;
  Chips(props: ChipsProps): HTMLDivElement;
  Ladder(props: LadderProps): HTMLOListElement;
  RulesList(props: RulesListProps): HTMLOListElement;
  PortsList(props: PortsListProps): HTMLDivElement;
  Gauge(props: GaugeProps): HTMLDivElement;
  EmptyState(props: EmptyStateProps): HTMLDivElement;
  PageTabs(props: PageTabsProps): HTMLElement;
  Statusline(props: StatuslineProps): HTMLAnchorElement;
}

declare global { interface Window { VGrowCockpit: VGrowCockpitNamespace } }
