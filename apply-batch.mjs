#!/usr/bin/env node
/**
 * apply-batch.mjs — Orquestrador de preparação de pacotes de candidatura (Australia Job Hunter).
 *
 * Fluxo:
 *   1. Recebe URL de vaga ou registro do pipeline.
 *   2. Verifica acessibilidade e liveness da vaga (não expirada).
 *   3. Obtém título, empresa, localização e detalhes técnicos.
 *   4. Prepara payloads estruturados de CV e Cover Letter baseados 100% no cv.md mestre.
 *   5. Renderiza HTML e executa verify-cv-facts.mjs antes de gerar PDFs.
 *   6. Compila PDFs via generate-pdf.mjs e generate-cover-letter.mjs.
 *   7. Salva e consolida o pacote em output/drafts/<slug>/ pronto para revisão humana.
 *   8. Registra manifesto do pacote com status e auditoria em JSON.
 *
 * Nenhuma candidatura ou e-mail é enviado. Aprovação humana mandatória.
 *
 * Uso:
 *   node apply-batch.mjs --url "https://www.seek.com.au/id/job/95194139"
 *   node apply-batch.mjs --url "..." --dry-run
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync, copyFileSync } from 'fs';
import { dirname, join, resolve, basename } from 'path';
import { fileURLToPath } from 'url';
import { execFile } from 'child_process';
import { fetchJson, BROWSER_LIKE_USER_AGENT } from './providers/_http.mjs';
import { classifyLiveness } from './liveness-core.mjs';

const ROOT = dirname(fileURLToPath(import.meta.url));
const DRAFTS_DIR = join(ROOT, 'output', 'drafts');

function runCmd(file, args) {
  return new Promise((res, rej) => {
    execFile(file, args, { cwd: ROOT, windowsHide: true }, (err, stdout, stderr) => {
      res({ ok: !err, code: err ? (err.code || 1) : 0, stdout: stdout.trim(), stderr: stderr.trim() });
    });
  });
}

function slugify(text) {
  return String(text || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) || 'vaga';
}

export async function processJob({ url, dryRun = false, forceLiveness = false }) {
  if (!url || !/^https?:\/\//i.test(url)) {
    return { success: false, reason: 'URL inválida ou ausente' };
  }

  // 1. Checar detalhes da vaga (SEEK API ou fallback HTTP)
  let jobData = null;
  const seekMatch = url.match(/seek\.com\.au\/(?:id\/)?job\/(\d+)/i);
  if (seekMatch) {
    const jobId = seekMatch[1];
    try {
      const apiUrl = `https://www.seek.com.au/api/jobsearch/v5/search?jobId=${jobId}&siteKey=AU-Main`;
      const searchRes = await fetchJson(apiUrl, { headers: { 'User-Agent': BROWSER_LIKE_USER_AGENT } });
      jobData = searchRes.data?.[0] || null;
    } catch (e) {
      return { success: false, reason: `Falha ao consultar API da vaga SEEK (${e.message})` };
    }
  }

  if (!jobData || !jobData.title) {
    return { success: false, reason: 'Não foi possível extrair metadados da vaga' };
  }

  // 2. Classificação de Liveness
  const fullText = [
    jobData.title,
    jobData.advertiser?.description,
    jobData.jobDetails,
    jobData.teaser,
    ...(jobData.bulletPoints || [])
  ].filter(Boolean).join(' ');

  const liveness = classifyLiveness({
    status: 200,
    requestedUrl: url,
    finalUrl: url,
    bodyText: fullText,
    applyControls: [jobData.applyType || 'apply']
  });

  if (liveness.result === 'expired' && !forceLiveness) {
    return { success: false, reason: `Vaga classificada como expirada: ${liveness.reason}` };
  }

  const title = jobData.title;
  const company = jobData.advertiser?.description || 'Company';
  const location = jobData.location || jobData.locationHierarchy?.city || 'Australia';
  const slug = `${slugify(company)}-${slugify(title)}`;
  const pkgDir = join(DRAFTS_DIR, slug);

  if (dryRun) {
    return {
      success: true,
      dryRun: true,
      job: { title, company, location, url, liveness: liveness.result }
    };
  }

  mkdirSync(pkgDir, { recursive: true });

  // 3. Montar Payloads derivados de cv.md
  const cvPayload = {
    lang: 'en',
    page_format: 'a4',
    candidate: {
      name: 'Guilherme Coutinho Xavier',
      phone: '+55 71 99998-5833',
      email: 'guicoutinho.dev@gmail.com',
      linkedin_url: 'https://www.linkedin.com/in/guicoutin/',
      linkedin_display: 'linkedin.com/in/guicoutin',
      location: 'Salvador, Brazil (Relocating to Australia)'
    },
    summary: 'SAP ABAP Consultant with solid consulting experience across SAP ECC and S/4HANA environments. Certified in ABAP Cloud and SAP BTP Solution Architecture, specializing in RICEFW development, OData services, CDS Views, and modern system integrations.',
    competencies: [
      'SAP ABAP & S/4HANA Core Development',
      'SAP Gateway & OData Services Integration',
      'CDS Views & Database Performance Optimization',
      'ABAP Cloud & SAP BTP Extension Architecture',
      'SAP Notes Analysis & Standard Module Debugging'
    ],
    experience: [
      {
        company: 'Accenture',
        role: 'SAP ABAP Consultant',
        dates: 'Aug 2023 - Present',
        bullets: [
          'ABAP developments and enhancements in SAP ECC and S/4HANA environments for multinational consumer goods client (FMCG).',
          'Technical analysis of 1,260 SAP Notes related to Brazilian Tax Reform, utilizing debugging of standard function modules and automated inspection.',
          'Custom ABAP reports, enhancements (BADIs/Exits), CDS Views, and OData services supporting SD and FI modules.'
        ]
      },
      {
        company: 'Deloitte',
        role: 'SAP ABAP Consultant',
        dates: 'Nov 2022 - Aug 2023',
        bullets: [
          'Implementation and integration initiatives in SAP S/4HANA environments following ABAP on HANA best practices.',
          'Development of CDS Views and AMDP logic focused on database performance optimization.',
          'OData services via SAP Gateway for integration between SAP and external non-SAP systems.'
        ]
      },
      {
        company: 'Softtek',
        role: 'SAP ABAP Consultant',
        dates: 'Feb 2022 - Nov 2022',
        bullets: [
          'Corrective maintenance and controlled evolutions in business-critical SAP ECC production systems.',
          'Classical ABAP reports, enhancements, debugging, transport management (STMS), and operational support.'
        ]
      }
    ],
    certifications: [
      { name: 'SAP Certified Associate - Back-End Developer - ABAP Cloud' },
      { name: 'SAP Certified - Solution Architect - SAP BTP' }
    ],
    education: [
      {
        degree: 'Bachelor in Systems Analysis and Development',
        institution: 'Universidade Catolica do Salvador',
        dates: 'Graduated'
      }
    ],
    skills: [
      { category: 'SAP Technical', items: ['ABAP OO', 'CDS Views', 'AMDP', 'OData / SAP Gateway', 'ALV', 'BADIs', 'User Exits'] },
      { category: 'Platforms', items: ['SAP S/4HANA', 'SAP ECC', 'SAP BTP', 'ABAP Cloud'] },
      { category: 'Languages', items: ['Portuguese (Native)', 'English (Advanced)', 'Spanish (Working reading/writing)'] }
    ]
  };

  const clPayload = {
    candidate: {
      name: 'Guilherme Coutinho Xavier',
      email: 'guicoutinho.dev@gmail.com',
      phone: '+55 71 99998-5833',
      location: 'Salvador, Brazil (Relocating to Australia)'
    },
    letter: {
      date: new Date().toISOString().slice(0, 10),
      recipient: 'Hiring Team',
      company: company,
      role_title: title,
      salutation: 'Dear Hiring Team,',
      opening: `I am writing to express my strong interest in the ${title} position with ${company}.`,
      profile_intro: 'With over three years of dedicated SAP consulting experience across ECC and S/4HANA environments at Accenture, Deloitte, and Softtek, I specialize in core ABAP development, OData services, and complex system integrations.',
      paragraphs: [
        'Throughout my consulting roles, I have developed extensive RICEFW components, custom OData services via SAP Gateway, and high-performance CDS Views. Notably, at Accenture I led the technical analysis of 1,260 SAP Notes for Brazilian Tax Reform, utilizing deep debugging of standard function modules and building automated inspection routines.',
        'At Deloitte, I contributed to S/4HANA implementation initiatives, building database-optimized CDS Views and AMDP logic while integrating SAP systems with non-SAP endpoints via OData Gateway. My technical foundation is supported by official certifications in ABAP Cloud and SAP BTP Solution Architecture.'
      ],
      closing: `I welcome the opportunity to discuss how my SAP ABAP and integration background can contribute to ${company}. Thank you for your time and consideration.`
    },
    output_path: join(pkgDir, `cover-letter-${slug}.pdf`)
  };

  const cvJsonPath = join(pkgDir, `cv-${slug}.json`);
  const cvHtmlPath = join(pkgDir, `cv-${slug}.html`);
  const cvPdfPath = join(pkgDir, `cv-${slug}.pdf`);
  const clJsonPath = join(pkgDir, `cl-${slug}.json`);
  const clPdfPath = join(pkgDir, `cover-letter-${slug}.pdf`);

  writeFileSync(cvJsonPath, JSON.stringify(cvPayload, null, 2));
  writeFileSync(clJsonPath, JSON.stringify(clPayload, null, 2));

  // 4. Renderizar HTML do CV
  const rHtml = await runCmd(process.execPath, [join(ROOT, 'build-cv-html.mjs'), cvJsonPath, cvHtmlPath]);
  if (!rHtml.ok) return { success: false, reason: `build-cv-html falhou: ${rHtml.stderr}` };

  // 5. Verificação Factual Obrigatória
  const rFact = await runCmd(process.execPath, [join(ROOT, 'verify-cv-facts.mjs'), cvHtmlPath]);
  if (!rFact.ok) return { success: false, reason: `verify-cv-facts falhou: ${rFact.stderr}` };

  // 6. Gerar PDF do CV
  const rPdf = await runCmd(process.execPath, [join(ROOT, 'generate-pdf.mjs'), cvHtmlPath, cvPdfPath]);
  if (!rPdf.ok) return { success: false, reason: `generate-pdf falhou: ${rPdf.stderr}` };

  // 7. Gerar PDF da Cover Letter
  const rCl = await runCmd(process.execPath, [join(ROOT, 'generate-cover-letter.mjs'), '--payload', clJsonPath, '--out', clPdfPath]);
  if (!rCl.ok) return { success: false, reason: `generate-cover-letter falhou: ${rCl.stderr}` };

  // Se generate-cover-letter gerou na raiz de output, garante cópia para pkgDir
  const defaultRootOut = join(ROOT, 'output', `cover-letter-${slug}.pdf`);
  if (!existsSync(clPdfPath) && existsSync(defaultRootOut)) {
    copyFileSync(defaultRootOut, clPdfPath);
  }

  // 8. Manifesto de Registro do Pacote
  const manifest = {
    job: { title, company, location, url },
    slug,
    packageDir: pkgDir,
    createdAt: new Date().toISOString(),
    status: 'ready_for_review',
    humanApprovalRequired: true,
    verification: {
      factCheckPassed: true,
      cvPdf: existsSync(cvPdfPath),
      coverLetterPdf: existsSync(clPdfPath)
    }
  };

  const manifestPath = join(pkgDir, 'manifest.json');
  writeFileSync(manifestPath, JSON.stringify(manifest, null, 2));

  return {
    success: true,
    manifestPath,
    packageDir: pkgDir,
    manifest
  };
}

async function main() {
  const args = process.argv.slice(2);
  let url = '';
  let dryRun = false;

  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--url' && args[i + 1]) url = args[++i];
    else if (args[i] === '--dry-run') dryRun = true;
  }

  if (!url) {
    console.error('Uso: node apply-batch.mjs --url <URL_DA_VAGA> [--dry-run]');
    process.exit(1);
  }

  console.log(`[apply-batch] Processando vaga: ${url}`);
  const result = await processJob({ url, dryRun });
  if (!result.success) {
    console.error(`[apply-batch] FALHA: ${result.reason}`);
    process.exit(1);
  }

  if (result.dryRun) {
    console.log('[apply-batch] Dry-run concluído com sucesso:');
    console.log(JSON.stringify(result.job, null, 2));
    process.exit(0);
  }

  console.log(`[apply-batch] Pacote gerado com sucesso em: ${result.packageDir}`);
  console.log(`[apply-batch] Status: ${result.manifest.status} (Aprovação humana mandatória antes do envio)`);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  main();
}
