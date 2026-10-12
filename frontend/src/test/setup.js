import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

// Vitest does not expose global hooks in this project, so register cleanup
// explicitly to ensure one rendered route cannot leak into the next test.
afterEach(cleanup);

// jsdom does not implement visual scrolling. The workspace calls this after
// timeline updates, so provide the browser method during component tests.
Element.prototype.scrollIntoView = vi.fn();
