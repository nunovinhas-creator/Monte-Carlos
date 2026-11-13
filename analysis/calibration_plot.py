"""Offline publication figure. Matplotlib is a research-only dependency."""
import os
from pathlib import Path


def plot(result, output):
    from project_paths import ROOT
    os.environ.setdefault('MPLCONFIGDIR', str(ROOT/'work/.matplotlib'))
    os.environ.setdefault('XDG_CACHE_HOME', str(ROOT/'work/.cache'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['svg.hashsalt'] = 'Monte-Carlos-20261113'
    fig, axes = plt.subplots(1,2,figsize=(11,4.5),layout='constrained')
    for ax,(partition,label) in zip(axes,[('validation_scores','Validação'),('holdout_scores','Holdout final')]):
        ax.plot([0,1],[0,1],color='gray',linestyle='--',label='Identidade')
        for name,values in result[partition].items():
            bins=[b for b in values['reliability'] if b['n']]
            ax.plot([b['predicted'] for b in bins],[b['observed'] for b in bins],marker='o',label=name)
        ax.set(xlim=(0,1),ylim=(0,1),xlabel='Probabilidade média prevista',ylabel='Frequência observada',title=label)
        ax.grid(alpha=.2); ax.legend(fontsize=8)
    fig.suptitle(f'Over 2,5 — calibração offline, mesma amostra por partição\nHoldout n={result["holdout"]["n"]}; exploratório histórico; sem ativação em produção',fontsize=11)
    fig.savefig(Path(output)/'reliability.png',dpi=160,metadata={'Software':'Monte-Carlos offline research'})
    plt.close(fig)
