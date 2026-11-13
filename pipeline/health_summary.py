"""Coverage diagnostics always shown, independently of workflow success."""
import json
import os

from pipeline.integrity import audit
from pipeline.coverage import coverage_report
from project_paths import DB_PATH


def main():
    result = audit(DB_PATH)
    coverage = result['coverage_7d']
    diagnostic = coverage_report(DB_PATH)
    text = '\n'.join([
        '## Saúde do pipeline e cobertura de odds', '',
        f"- Jogos v2 liquidados em 7 dias: {result['coverage_n']}",
        f"- Com odds pré-jogo: {result['coverage_quoted']}",
        f'- Cobertura: {coverage:.1%}' if coverage is not None else '- Cobertura: N/D (sem amostra)',
        '- Um workflow verde não demonstra cobertura satisfatória nem validação científica.',
        '- Piso de bloqueio operacional: 25% com n≥20. Referência para futuros protocolos científicos: ≥80%.',
        '- Cobertura abaixo de 80%: leitura limitada por seleção de dados.' if coverage is not None and coverage<.8 else '',
        '- Auditoria: '+json.dumps(result,ensure_ascii=False), '',
        '- Diagnóstico por liga/dia e decisões de captura: '+json.dumps(diagnostic,ensure_ascii=False), '',
    ])
    print(text)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as stream:
            stream.write(text)


if __name__ == '__main__':
    main()
