import test from 'node:test';
import assert from 'node:assert/strict';
import { formatSourcePages } from '../src/utils/sourcePages.ts';

test('cross-page citations show exact pages and support single-page sources', () => {
  assert.equal(formatSourcePages([3, 2, 3, 4], 2), '第2–4页');
  assert.equal(formatSourcePages([1, 3, 4]), '第1、3–4页');
  assert.equal(formatSourcePages([], 2), '第2页');
  assert.equal(formatSourcePages(undefined, undefined), '');
});
