import assert from 'node:assert/strict';
import test from 'node:test';
import greenhouse from '../../providers/greenhouse.mjs';
import ashby from '../../providers/ashby.mjs';

test('Greenhouse requests and returns public job-post content', async () => {
  let requestedUrl = '';
  const jobs = await greenhouse.fetch(
    { name: 'Example Greenhouse', api: 'https://boards-api.greenhouse.io/v1/boards/example/jobs' },
    {
      fetchJson: async (url) => {
        requestedUrl = url;
        return { jobs: [{ title: 'Farm Hand', absolute_url: 'https://jobs.example/1', content: '<p>Harvest duties</p>' }] };
      },
    },
  );
  assert.equal(new URL(requestedUrl).searchParams.get('content'), 'true');
  assert.equal(jobs[0].description, '<p>Harvest duties</p>');
});

test('Ashby preserves its public plain-text job description', async () => {
  const jobs = await ashby.fetch(
    { name: 'Example Ashby', api: 'https://api.ashbyhq.com/posting-api/job-board/example?includeCompensation=true' },
    {
      fetchJson: async () => ({ jobs: [{ title: 'Seasonal Picker', jobUrl: 'https://jobs.example/2', descriptionPlain: 'Pick fruit in season.' }] }),
      sleep: async () => {},
    },
  );
  assert.equal(jobs[0].description, 'Pick fruit in season.');
});
