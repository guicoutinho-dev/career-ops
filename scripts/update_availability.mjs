import fs from 'fs';

// 1. Update config/profile.yml
let profileYml = fs.readFileSync('config/profile.yml', 'utf-8');
if (!profileYml.includes('availability:')) {
  profileYml = profileYml.replace(
    '  visa_status: "Eligible for Working Holiday Visa (WHV Subclass 462) in Australia — independent full work rights"',
    '  visa_status: "Eligible for Working Holiday Visa (WHV Subclass 462) in Australia — independent full work rights"\n  availability: "Late November to Early December 2026 (approx. 25 Nov - 05 Dec 2026)"'
  );
  fs.writeFileSync('config/profile.yml', profileYml, 'utf-8');
  console.log('Updated config/profile.yml');
}

// 2. Update modes/_profile.md
let profileMd = fs.readFileSync('modes/_profile.md', 'utf-8');
if (!profileMd.includes('Late November - Early December 2026')) {
  profileMd = profileMd.replace(
    '- Follow your actual availability from profile.yml\n- Specify timezone overlap in free-text fields',
    '- Follow your actual availability from profile.yml: Late November - Early December 2026 (approx. 25 Nov - 05 Dec 2026)\n- Specify timezone overlap in free-text fields'
  );
  fs.writeFileSync('modes/_profile.md', profileMd, 'utf-8');
  console.log('Updated modes/_profile.md');
}

// 3. Update interview-prep/voyages-housing-gardener.md
let prepMd = fs.readFileSync('interview-prep/voyages-housing-gardener.md', 'utf-8');
if (!prepMd.includes('Availability & Arrival Window')) {
  const insertTarget = '## 6. Questions to Ask Sarah Reed';
  const arrivalSection = `## 5.1 Availability & Arrival Window (Late November - Early December 2026)

* **Target Arrival / Start Window:** Late November to Early December 2026 (between **25 November and 05 December 2026**).
* **Rationale:** As of November 2, 2026, this realistic 3-4 week timeframe ensures smooth transition, visa finalization, ticket booking, and relocation logistics directly to Yulara / Ayers Rock Resort.
* **Candidate Talking Point for Screening Call:**
  > *"My target start window is late November to early December—ideally arriving between November 25th and December 5th. This allows me to finalize my travel logistics and hit the ground running for the peak summer operations."*

---

## 6. Questions to Ask Sarah Reed`;
  prepMd = prepMd.replace(insertTarget, arrivalSection);
  fs.writeFileSync('interview-prep/voyages-housing-gardener.md', prepMd, 'utf-8');
  console.log('Updated interview-prep/voyages-housing-gardener.md');
}
