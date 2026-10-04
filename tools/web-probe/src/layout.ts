import type { Page } from 'playwright-core';

export type Bounds = { left: number; right: number; top: number; bottom: number };

export type ActionButton = Bounds & {
    name: string;
    disabled: boolean;
    visible: boolean;
};

export type OverviewGeometry = {
    viewport: number;
    document: number;
    actions: Bounds;
    buttons: ActionButton[];
};

export type SetsGeometry = {
    viewport: number;
    document: number;
    maximum: number;
    scroll: number;
    firstVisible: boolean;
    lastVisible: boolean;
    toolbarVisible: boolean;
    headersFit: boolean;
};

export function requireCondition(condition: unknown, message: string): asserts condition {
    if (!condition) throw new Error(message);
}

export async function checkOverviewLayout(page: Page, width: number): Promise<void> {
    const geometry = await page.evaluate((): OverviewGeometry | null => {
        const actions = document.querySelector<HTMLElement>(
            'omv-intuition-form-page > mat-card:has(.protondrive-status-heading) > mat-card-actions',
        );
        if (!actions) return null;
        const bounds = actions.getBoundingClientRect();
        return {
            viewport: innerWidth,
            document: document.documentElement.scrollWidth,
            actions: { left: bounds.left, right: bounds.right, top: bounds.top, bottom: bounds.bottom },
            buttons: [...actions.querySelectorAll<HTMLButtonElement>('button')].map(button => {
                const box = button.getBoundingClientRect();
                return {
                    name: button.textContent?.trim() ?? '',
                    left: box.left,
                    right: box.right,
                    top: box.top,
                    bottom: box.bottom,
                    disabled: button.disabled,
                    visible: getComputedStyle(button).display !== 'none',
                };
            }),
        };
    });
    requireCondition(geometry !== null, 'Overview action footer is missing');
    requireCondition(
        geometry.document <= geometry.viewport,
        `${width}px page has horizontal overflow: ${JSON.stringify(geometry)}`,
    );
    const buttons = geometry.buttons.filter(button => button.visible);
    requireCondition(
        geometry.buttons.length === 7,
        `Expected seven overview actions, found ${geometry.buttons.length}`,
    );
    requireCondition(
        geometry.buttons.every(button => button.disabled || button.visible),
        `${width}px hides an enabled action`,
    );
    requireCondition(buttons.length >= 2, 'Overview has no visible navigation actions');
    if (width <= 600) {
        requireCondition(
            geometry.buttons.every(button => !button.disabled || !button.visible),
            `${width}px shows disabled actions`,
        );
        const settings = buttons.find(button => button.name === 'Settings');
        const sets = buttons.find(button => button.name === 'Backup sets');
        requireCondition(settings && sets, 'Mobile overview navigation actions are missing');
        requireCondition(Math.abs(settings.top - sets.top) <= 1, `${width}px navigation actions do not share a row`);
    }
    for (const button of buttons) {
        requireCondition(
            button.left >= geometry.actions.left - 1 && button.right <= geometry.actions.right + 1,
            `${width}px action ${button.name} escapes its footer`,
        );
    }
    for (const [index, first] of buttons.entries()) {
        if (!first) continue;
        for (const second of buttons.slice(index + 1)) {
            const overlap = Math.min(first.right, second.right) - Math.max(first.left, second.left);
            const vertical = Math.min(first.bottom, second.bottom) - Math.max(first.top, second.top);
            requireCondition(
                overlap <= 1 || vertical <= 1,
                `${width}px actions overlap: ${first.name}, ${second.name}`,
            );
        }
    }
}

export async function checkSetsStart(page: Page, width: number): Promise<boolean> {
    const geometry = await page.evaluate((): SetsGeometry | null => {
        const table = document.querySelector<HTMLElement>('omv-intuition-datatable-page omv-datatable');
        const toolbar = table?.querySelector<HTMLElement>('.action-toolbar');
        const headers = [...(table?.querySelectorAll<HTMLElement>('datatable-header-cell') ?? [])];
        if (!table || !toolbar || headers.length !== 5) return null;
        const first = headers[1];
        if (!first) return null;
        const viewport = table.getBoundingClientRect();
        const cell = first.getBoundingClientRect();
        const create = toolbar.querySelector<HTMLButtonElement>('button:not(:disabled)');
        const control = create?.getBoundingClientRect();
        return {
            viewport: innerWidth,
            document: document.documentElement.scrollWidth,
            maximum: table.scrollWidth - table.clientWidth,
            scroll: table.scrollLeft,
            firstVisible: cell.left >= viewport.left - 1 && cell.right <= viewport.right + 1,
            lastVisible: false,
            toolbarVisible: !!control && control.left >= viewport.left - 1 && control.right <= viewport.right + 1,
            headersFit: headers.every(header => header.scrollWidth <= header.clientWidth + 1),
        };
    });
    requireCondition(geometry !== null, 'Backup sets table or action toolbar is missing');
    requireCondition(
        geometry.document <= geometry.viewport,
        `${width}px Backup sets page overflows: ${JSON.stringify(geometry)}`,
    );
    requireCondition(geometry.toolbarVisible, `${width}px Backup sets action toolbar is clipped`);
    requireCondition(geometry.headersFit, `${width}px Backup sets headers are clipped: ${JSON.stringify(geometry)}`);
    if (geometry.maximum > 0) {
        requireCondition(geometry.scroll === 0, `${width}px table does not start at its first column`);
        requireCondition(geometry.firstVisible, `${width}px table columns are clipped: ${JSON.stringify(geometry)}`);
    }
    return geometry.maximum > 0;
}

export async function scrollSetsRight(page: Page, width: number): Promise<void> {
    const geometry = await page.evaluate(async (): Promise<SetsGeometry | null> => {
        const table = document.querySelector<HTMLElement>('omv-intuition-datatable-page omv-datatable');
        const toolbar = table?.querySelector<HTMLElement>('.action-toolbar');
        const headers = [...(table?.querySelectorAll<HTMLElement>('datatable-header-cell') ?? [])];
        if (!table || !toolbar || headers.length !== 5) return null;
        table.scrollLeft = table.scrollWidth;
        await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
        const last = headers[4];
        if (!last) return null;
        const viewport = table.getBoundingClientRect();
        const cell = last.getBoundingClientRect();
        const create = toolbar.querySelector<HTMLButtonElement>('button:not(:disabled)');
        const control = create?.getBoundingClientRect();
        return {
            viewport: innerWidth,
            document: document.documentElement.scrollWidth,
            maximum: table.scrollWidth - table.clientWidth,
            scroll: table.scrollLeft,
            firstVisible: false,
            lastVisible: cell.left >= viewport.left - 1 && cell.right <= viewport.right + 1,
            toolbarVisible: !!control && control.left >= viewport.left - 1 && control.right <= viewport.right + 1,
            headersFit: headers.every(header => header.scrollWidth <= header.clientWidth + 1),
        };
    });
    requireCondition(geometry !== null, 'Backup sets table or action toolbar is missing after scrolling');
    requireCondition(
        geometry.maximum > 0 && geometry.scroll === geometry.maximum && geometry.lastVisible && geometry.toolbarVisible,
        `${width}px cannot reveal the final table column without losing the toolbar: ${JSON.stringify(geometry)}`,
    );
}
