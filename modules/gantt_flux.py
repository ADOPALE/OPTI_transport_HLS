"""
gantt_flux.py  —  Synthèse transport + Gantt des postes chauffeurs
===================================================================

Reconstruit la séquence détaillée de chaque poste chauffeur depuis
les missions OR-Tools et l'affiche sous forme de timeline Gantt Plotly.

Séquence par mission :
  Trajet vide  →  Mise à quai  →  Chargement
  →  Trajet plein  →  Mise à quai  →  Déchargement

Appelé depuis app.py :
    from modules.gantt_flux import afficher_synthese_transport
    afficher_synthese_transport()
"""

import math
from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


# ─────────────────────────────────────────────────────────────────
# COULEURS ET CONFIG ÉTAPES
# ─────────────────────────────────────────────────────────────────

ETAPES = {
    "prise_poste"  : ("Prise de poste", "#4A90D9"),
    "trajet_vide"  : ("Trajet vide",    "#B0BEC5"),
    "mise_a_quai"  : ("Mise à quai",    "#26C6DA"),
    "chargement"   : ("Chargement",     "#66BB6A"),
    "trajet_plein" : ("Trajet plein",   "#FFA726"),
    "dechargement" : ("Livraison",      "#EF5350"),
    "attente"      : ("Attente",        "#CE93D8"),
    "pause"        : ("Pause",          "#FF7043"),
    "fin_poste"    : ("Fin de poste",   "#78909C"),
}
COULEURS = {v[0]: v[1] for v in ETAPES.values()}


# ─────────────────────────────────────────────────────────────────
# UTILITAIRES
# ─────────────────────────────────────────────────────────────────

def _min_to_dt(minutes: float) -> datetime:
    """Convertit minutes depuis minuit en datetime (date fictive 2000-01-01)."""
    return datetime(2000, 1, 1) + timedelta(minutes=float(minutes))


def _to_min(val, default: float = 0.0) -> float:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return default
    if hasattr(val, 'hour'):
        return val.hour * 60 + val.minute + val.second / 60
    try:
        return float(val) * 1440
    except Exception:
        return default


def _duree_trajet(df_duree: pd.DataFrame, site_a: str, site_b: str) -> float:
    """Durée en minutes entre deux sites (depuis matrice normalisée)."""
    a, b = site_a.strip().upper(), site_b.strip().upper()
    if a == b:
        return 0.0
    try:
        return float(df_duree.loc[a, b])
    except Exception:
        return 15.0


def _params_veh(df_vehicules: pd.DataFrame, v_type: str):
    """Retourne (t_quai_min, t_sans_quai_min/cont, t_avec_quai_min/cont)."""
    row = df_vehicules[
        df_vehicules.iloc[:, 0].str.strip().str.upper() == v_type.strip().upper()
    ]
    if row.empty:
        return 10.0, 25 / 60, 15 / 60
    r = row.iloc[0]
    return (
        _to_min(r.get('Temps de mise à quai - manœuvre, contact/admin (minutes)'), 10.0),
        _to_min(r.get('Manutention sans quai (minutes / contenants)'), 25 / 60),
        _to_min(r.get('Manutention avec quai (minutes / contenants)'), 15 / 60),
    )


def _a_quai(df_sites: pd.DataFrame, site: str) -> bool:
    """Vérifie si un site possède un quai de chargement."""
    col_lib = next(
        (c for c in df_sites.columns if 'libel' in c.lower()),
        df_sites.columns[0]
    )
    row = df_sites[
        df_sites[col_lib].astype(str).str.strip().str.upper() == site.strip().upper()
    ]
    if row.empty:
        return False
    return str(row['Présence de quai'].values[0]).strip().upper() == 'OUI'


# ─────────────────────────────────────────────────────────────────
# RECONSTRUCTION DE LA SÉQUENCE GANTT PAR POSTE
# ─────────────────────────────────────────────────────────────────

def reconstruire_sequence(
    poste,
    df_duree: pd.DataFrame,
    df_vehicules: pd.DataFrame,
    df_sites: pd.DataFrame,
    params_logistique: dict,
) -> list[dict]:
    """
    Reconstruit la séquence détaillée d'étapes d'un poste chauffeur.

    Pour chaque mission :
      1. Trajet vide depuis site précédent
      2. Mise à quai (si quai présent)
      3. Chargement (t_quai × taux + t_manu × nb_cont)
      4. Trajet plein vers destination
      5. Mise à quai (si quai présent)
      6. Déchargement

    Les heures OR-Tools servent d'ANCRE pour le début de chaque opération
    sur site — on ne repart pas d'une accumulation de durées.

    Retourne list[dict] avec clés : type, h_debut, h_fin, info
    """
    rh          = params_logistique.get('rh', {})
    t_prise     = float(rh.get('temps_fixes_prise', 20))
    t_fin_p     = float(rh.get('temps_fixes_fin',   15))
    t_pause     = float(rh.get('pause', 30))
    seuil_pause = 180.0
    amp_max     = float(rh.get('amplitude_totale', 450))

    t_quai, t_sans, t_avec = _params_veh(df_vehicules, poste.v_type)

    etapes      = []
    missions    = sorted(poste.missions, key=lambda m: m['heure'])
    h_debut     = poste.h_debut
    h_fin_off   = h_debut + amp_max  # fin officielle du poste

    # 1. Prise de poste
    etapes.append({
        'type'   : 'prise_poste',
        'h_debut': h_debut,
        'h_fin'  : h_debut + t_prise,
        'info'   : 'Préparation / Check véhicule',
    })
    curseur      = h_debut + t_prise
    site_courant = 'HLS'
    charge       = 0
    pause_faite  = False

    for m in missions:
        h_ortools = m['heure']              # heure OR-Tools = arrivée sur origine
        origine   = m.get('origine',     m.get('site', 'HLS'))
        dest      = m.get('destination', m.get('site', 'HLS'))
        nb_cont   = m['nb_contenants']
        est_comp  = m.get('est_complet', True)
        capa_utile= m.get('capa_utile',  nb_cont)
        ps        = m.get('propre_sale', '')

        taux_j = 1.0 if est_comp else nb_cont / max(1, capa_utile)

        # Durées réelles
        quai_orig = _a_quai(df_sites, origine)
        quai_dest = _a_quai(df_sites, dest)
        t_q_orig  = t_quai * taux_j
        t_q_dest  = t_quai * taux_j
        t_m_orig  = nb_cont * (t_avec if quai_orig else t_sans)
        t_m_dest  = nb_cont * (t_avec if quai_dest else t_sans)
        dur_trajet_vide  = _duree_trajet(df_duree, site_courant, origine)
        dur_trajet_plein = _duree_trajet(df_duree, origine, dest)

        # ── Trajet vide vers l'origine ──────────────────────────────────
        h_depart = max(curseur, h_ortools - dur_trajet_vide - t_q_orig - t_m_orig)
        if h_depart > curseur + 0.5:
            etapes.append({
                'type'   : 'attente',
                'h_debut': curseur,
                'h_fin'  : h_depart,
                'info'   : f'Attente sur {site_courant}',
            })

        # Pause si seuil atteint
        if not pause_faite and (h_depart - h_debut) > seuil_pause:
            etapes.append({
                'type'   : 'pause',
                'h_debut': h_depart,
                'h_fin'  : h_depart + t_pause,
                'info'   : 'Pause obligatoire',
            })
            h_depart   += t_pause
            pause_faite = True

        if dur_trajet_vide > 0.5:
            etapes.append({
                'type'   : 'trajet_vide' if charge == 0 else 'trajet_plein',
                'h_debut': h_depart,
                'h_fin'  : h_depart + dur_trajet_vide,
                'info'   : f'{site_courant} → {origine} ({dur_trajet_vide:.0f} min)',
            })
        curseur = h_depart + dur_trajet_vide

        # ── Mise à quai + Chargement sur l'origine ──────────────────────
        if t_q_orig > 0.1:
            etapes.append({
                'type'   : 'mise_a_quai',
                'h_debut': curseur,
                'h_fin'  : curseur + t_q_orig,
                'info'   : f'Mise à quai {origine} ({t_q_orig:.1f} min)',
            })
            curseur += t_q_orig

        etapes.append({
            'type'   : 'chargement',
            'h_debut': curseur,
            'h_fin'  : curseur + t_m_orig,
            'info'   : f'{nb_cont} {m["type_contenant"]} ({ps}) — {origine} ({t_m_orig:.1f} min)',
        })
        curseur += t_m_orig
        charge  += nb_cont

        # ── Trajet plein vers destination ────────────────────────────────
        if dur_trajet_plein > 0.5:
            etapes.append({
                'type'   : 'trajet_plein',
                'h_debut': curseur,
                'h_fin'  : curseur + dur_trajet_plein,
                'info'   : f'{origine} → {dest} ({dur_trajet_plein:.0f} min)',
            })
        curseur += dur_trajet_plein

        # ── Mise à quai + Déchargement sur la destination ───────────────
        if t_q_dest > 0.1:
            etapes.append({
                'type'   : 'mise_a_quai',
                'h_debut': curseur,
                'h_fin'  : curseur + t_q_dest,
                'info'   : f'Mise à quai {dest} ({t_q_dest:.1f} min)',
            })
            curseur += t_q_dest

        etapes.append({
            'type'   : 'dechargement',
            'h_debut': curseur,
            'h_fin'  : curseur + t_m_dest,
            'info'   : f'{nb_cont} {m["type_contenant"]} ({ps}) → {dest} ({t_m_dest:.1f} min)',
        })
        curseur     += t_m_dest
        charge       = max(0, charge - nb_cont)
        site_courant = dest

    # ── Retour HLS ───────────────────────────────────────────────────────
    if site_courant != 'HLS':
        dur_ret = _duree_trajet(df_duree, site_courant, 'HLS')
        if dur_ret > 0.5:
            etapes.append({
                'type'   : 'trajet_vide',
                'h_debut': curseur,
                'h_fin'  : curseur + dur_ret,
                'info'   : f'Retour HLS ({dur_ret:.0f} min)',
            })
            curseur += dur_ret

    # ── Attente jusqu'à fin officielle ──────────────────────────────────
    t_fin_off = h_fin_off - t_fin_p
    if curseur < t_fin_off - 1:
        etapes.append({
            'type'   : 'attente',
            'h_debut': curseur,
            'h_fin'  : t_fin_off,
            'info'   : 'En attente de fin de poste',
        })

    # ── Fin de poste ─────────────────────────────────────────────────────
    etapes.append({
        'type'   : 'fin_poste',
        'h_debut': h_fin_off - t_fin_p,
        'h_fin'  : h_fin_off,
        'info'   : 'Nettoyage / Clôture',
    })

    return etapes


# ─────────────────────────────────────────────────────────────────
# TRACÉ PLOTLY
# ─────────────────────────────────────────────────────────────────

def tracer_gantt(
    postes: list,
    sequences: dict,          # {poste_id: list[dict]}
    h_min_min: float = 360,   # 6h00
    h_max_min: float = 1260,  # 21h00
) -> go.Figure:
    """
    Construit la figure Plotly Gantt depuis les séquences pré-calculées.
    Une seule trace par type d'étape (légende unique via legendgroup).
    """
    # Construire le DataFrame de toutes les étapes
    rows = []
    for p in postes:
        seq = sequences.get(p.poste_id, [])
        label = p.poste_id
        for e in seq:
            type_e  = e['type']
            label_e = ETAPES.get(type_e, (type_e, '#888'))[0]
            duree   = e['h_fin'] - e['h_debut']
            if duree < 0.5:
                continue
            rows.append({
                'Poste'   : label,
                'Étape'   : label_e,
                'h_debut' : e['h_debut'],
                'h_fin'   : e['h_fin'],
                'duree'   : duree,
                'info'    : e.get('info', ''),
            })

    if not rows:
        return go.Figure()

    df = pd.DataFrame(rows)
    fig = go.Figure()

    for etape, couleur in COULEURS.items():
        df_e = df[df['Étape'] == etape]
        if df_e.empty:
            continue
        first = True
        for _, row in df_e.iterrows():
            fig.add_trace(go.Bar(
                name            = etape,
                y               = [row['Poste']],
                x               = [row['duree']],
                base            = [row['h_debut']],
                orientation     = 'h',
                marker_color    = couleur,
                text            = row['info'] if row['duree'] > 15 else '',
                textposition    = 'inside',
                insidetextanchor= 'middle',
                hovertemplate   = (
                    f"<b>{etape}</b><br>"
                    f"{int(row['h_debut']//60):02d}h{int(row['h_debut']%60):02d}"
                    f" → {int(row['h_fin']//60):02d}h{int(row['h_fin']%60):02d}"
                    f" ({row['duree']:.0f} min)<br>"
                    f"{row['info']}<extra></extra>"
                ),
                showlegend      = first,
                legendgroup     = etape,
            ))
            first = False

    # Axe X en heures réelles
    tickvals = list(range(int(h_min_min), int(h_max_min) + 1, 60))
    ticktext = [f"{v//60:02d}h00" for v in tickvals]

    fig.update_layout(
        barmode     = 'overlay',
        height      = max(300, len(postes) * 70 + 150),
        xaxis       = dict(
            tickvals = tickvals,
            ticktext = ticktext,
            title    = 'Heure',
            range    = [h_min_min, h_max_min],
        ),
        yaxis       = dict(title='Poste', autorange='reversed'),
        legend      = dict(
            orientation = 'h',
            yanchor     = 'bottom',
            y           = 1.02,
            xanchor     = 'left',
            x           = 0,
        ),
        margin      = dict(l=10, r=10, t=80, b=40),
        plot_bgcolor  = '#1a1a2e',
        paper_bgcolor = 'rgba(0,0,0,0)',
        font_color    = 'white',
    )
    return fig


# ─────────────────────────────────────────────────────────────────
# FONCTION PRINCIPALE APPELÉE DEPUIS APP.PY
# ─────────────────────────────────────────────────────────────────

def afficher_synthese_transport():
    """Point d'entrée principal — appelé depuis app.py."""
    st.title("🚚 Synthèse Transport — Optimisation OR-Tools")

    # ── Vérification des prérequis ────────────────────────────────────────
    donnees_ok = (
        "data" in st.session_state
        and all(k in st.session_state["data"]
                for k in ["m_flux", "param_vehicules", "param_contenants",
                          "param_sites", "matrice_duree"])
    )
    params_ok = "params_logistique" in st.session_state

    if not donnees_ok:
        st.warning("⚠️ Importez vos données Excel avant de lancer la simulation.")
        return
    if not params_ok:
        st.warning("⚠️ Validez vos paramètres dans l'onglet 'Véhicules et paramètres'.")
        return

    from modules.flux_engine import (
        run_flux_optimization, verifier_faisabilite, precalculer_capacites
    )

    # ── Contrôles de lancement ────────────────────────────────────────────
    col_j, col_t = st.columns([2, 1])
    with col_j:
        jour_choisi = st.selectbox(
            "Jour à simuler",
            ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"],
            key="flux_jour_choisi"
        )
    with col_t:
        time_limit = st.slider(
            "Budget temps OR-Tools (s)", 10, 300, 60, 10,
            help="Par type de véhicule. Plus élevé = meilleure solution."
        )

    # ── Vérification de faisabilité ───────────────────────────────────────
    if st.button("🔍 Vérifier la faisabilité", use_container_width=True):
        with st.spinner("Vérification en cours..."):
            try:
                cap = precalculer_capacites(
                    st.session_state["data"]["param_vehicules"],
                    st.session_state["data"]["param_contenants"],
                )
                ctrl = verifier_faisabilite(
                    df_flux           = st.session_state["data"]["m_flux"],
                    df_vehicules      = st.session_state["data"]["param_vehicules"],
                    df_contenants     = st.session_state["data"]["param_contenants"],
                    df_sites          = st.session_state["data"]["param_sites"],
                    capacites         = cap,
                    params_logistique = st.session_state["params_logistique"],
                    jour              = jour_choisi,
                )
                st.session_state["flux_controle"] = ctrl
            except Exception as e:
                st.error(f"Erreur : {e}")
                st.exception(e)

    if "flux_controle" in st.session_state:
        ctrl = st.session_state["flux_controle"]
        if ctrl["faisable"]:
            st.success(ctrl["resume"])
        else:
            st.warning(ctrl["resume"])
            with st.expander(f"📋 {ctrl['nb_flux_ko']} flux non faisables", expanded=False):
                if not ctrl["details_df"].empty:
                    for raison, grp in ctrl["details_df"].groupby("Problème"):
                        labels = {
                            "SITE_INCONNU"             : "🔴 Sites inconnus",
                            "AUCUN_VEHICULE_ACCESSIBLE": "🟠 Aucun véhicule accessible",
                            "AUCUNE_CAPACITE"          : "🟡 Capacité incompatible",
                        }
                        st.subheader(labels.get(raison, raison))
                        st.dataframe(grp.drop(columns=["Problème"]),
                                     use_container_width=True, hide_index=True)

    st.divider()

    # ── Lancement de l'optimisation ───────────────────────────────────────
    btn_label = ("🔄 Relancer la simulation"
                 if st.session_state.get("flux_sim_lancee")
                 else "🚀 Lancer la simulation")

    if st.button(btn_label, type="primary", use_container_width=True):
        with st.spinner("🧠 Optimisation OR-Tools en cours..."):
            try:
                resultats = run_flux_optimization(
                    df_flux           = st.session_state["data"]["m_flux"],
                    df_vehicules      = st.session_state["data"]["param_vehicules"],
                    df_contenants     = st.session_state["data"]["param_contenants"],
                    df_sites          = st.session_state["data"]["param_sites"],
                    matrice_duree     = st.session_state["data"]["matrice_duree"],
                    params_logistique = st.session_state["params_logistique"],
                    jour              = jour_choisi,
                    time_limit_seconds= time_limit,
                )
                st.session_state["flux_resultats"]  = resultats
                st.session_state["flux_sim_lancee"] = True
                st.rerun()
            except Exception as e:
                st.error(f"Erreur lors de l'optimisation : {e}")
                st.exception(e)

    # ── Affichage des résultats ───────────────────────────────────────────
    if not (st.session_state.get("flux_sim_lancee") and
            "flux_resultats" in st.session_state):
        return

    res     = st.session_state["flux_resultats"]
    rapport = res.get("rapport", {})
    postes  = res.get("postes", [])
    jour_res= res.get("jour", jour_choisi)

    st.divider()

    # ── Métriques globales ────────────────────────────────────────────────
    st.subheader(f"📊 Résultats — {jour_res}")

    nb_v = rapport.get("nb_vehicules_par_type", {})
    nb_p = rapport.get("nb_postes_par_type",   {})
    types = sorted(set(list(nb_v) + list(nb_p)))

    if types:
        cols = st.columns(len(types) + 2)
        for i, vt in enumerate(types):
            cols[i].metric(f"🚛 {vt}", f"{nb_v.get(vt,0)} véh.",
                           f"↑ {nb_p.get(vt,0)} poste(s)")
        cols[-2].metric("👤 Postes total", rapport.get("nb_postes_total", 0))
        cols[-1].metric("⏱️ Taux moyen",
                        f"{rapport.get('taux_moyen', 0):.1%}")

    # ── Jobs non planifiés ────────────────────────────────────────────────
    nb_np = rapport.get("nb_jobs_non_planifies", 0)
    if nb_np > 0:
        st.warning(f"⚠️ {nb_np} job(s) n'ont pas pu être planifiés.")
        with st.expander(f"🔍 Voir les {nb_np} jobs non planifiés", expanded=True):
            jobs_np = rapport.get("jobs_non_planifies_detail", [])
            rows_np = []
            for item in jobs_np:
                if isinstance(item, dict):
                    j, raison = item["job"], item.get("raison", "Non planifié")
                    vt = item.get("v_type", getattr(j, 'v_type', ''))
                else:
                    j, raison, vt = item, "Non planifié", getattr(item, 'v_type', '')
                rows_np.append({
                    "Job ID"    : j.job_id,
                    "Flux ID"   : j.flux_id,
                    "Origine"   : j.origine,
                    "Destination": j.destination,
                    "Contenant" : j.type_contenant,
                    "Qté"       : j.nb_contenants,
                    "P/S"       : j.propre_sale,
                    "Véhicule"  : vt,
                    "Fenêtre"   : (f"{int(j.h_dispo//60):02d}h{int(j.h_dispo%60):02d}"
                                   f" → {int(j.h_deadline//60):02d}h{int(j.h_deadline%60):02d}"),
                    "Raison"    : raison,
                })
            st.dataframe(pd.DataFrame(rows_np),
                         use_container_width=True, hide_index=True)
    else:
        st.success("✅ Tous les flux ont été planifiés.")

    if not postes:
        return

    # ── Tableau récapitulatif des postes ──────────────────────────────────
    st.divider()
    st.subheader("📅 Planning des postes chauffeurs")

    types_dispo = sorted({p.v_type for p in postes})
    type_filtre = st.selectbox("Filtrer par type de véhicule",
                               ["Tous"] + types_dispo, key="flux_type_filtre")
    postes_affiches = (postes if type_filtre == "Tous"
                       else [p for p in postes if p.v_type == type_filtre])

    rows_p = []
    for p in postes_affiches:
        rows_p.append({
            "Poste"          : p.poste_id,
            "Type véhicule"  : p.v_type,
            "Début"          : f"{int(p.h_debut//60):02d}h{int(p.h_debut%60):02d}",
            "Fin"            : f"{int(p.h_fin//60):02d}h{int(p.h_fin%60):02d}",
            "Amplitude (min)": round(p.amplitude),
            "Taux occupation": f"{p.taux_occupation:.1%}",
            "Nb missions"    : len(p.missions),
        })
    st.dataframe(pd.DataFrame(rows_p), use_container_width=True, hide_index=True)

    # ── Timeline Gantt ────────────────────────────────────────────────────
    st.divider()
    st.subheader("⏱️ Timeline des postes")

    # Récupérer données pour la reconstruction
    df_duree    = st.session_state["data"]["matrice_duree"].copy()
    df_vehicules= st.session_state["data"]["param_vehicules"]
    df_sites    = st.session_state["data"]["param_sites"]
    params_log  = st.session_state["params_logistique"]
    rh          = params_log.get("rh", {})

    # Normaliser la matrice de durées
    col0 = df_duree.columns[0]
    df_duree = df_duree.set_index(col0)
    df_duree.index   = df_duree.index.astype(str).str.strip().str.upper()
    df_duree.columns = df_duree.columns.astype(str).str.strip().str.upper()

    # Horaires de la journée
    h_min = int(_to_min(rh.get("h_prise_min"), 360) // 60) * 60
    h_max = int(_to_min(rh.get("h_fin_max"),  1260) // 60 + 1) * 60

    # Reconstruire la séquence de chaque poste
    with st.spinner("Construction du Gantt..."):
        sequences = {}
        for p in postes_affiches:
            try:
                sequences[p.poste_id] = reconstruire_sequence(
                    p, df_duree, df_vehicules, df_sites, params_log
                )
            except Exception as e:
                st.warning(f"Gantt impossible pour {p.poste_id} : {e}")
                sequences[p.poste_id] = []

    fig = tracer_gantt(postes_affiches, sequences, h_min, h_max)
    st.plotly_chart(fig, use_container_width=True)

    # ── Détail tabulaire d'un poste ───────────────────────────────────────
    st.divider()
    st.subheader("🔍 Détail d'un poste")

    poste_ids = [p.poste_id for p in postes_affiches]
    if not poste_ids:
        return

    poste_sel_id = st.selectbox("Choisir un poste", poste_ids, key="flux_poste_sel")
    poste_sel    = next((p for p in postes_affiches if p.poste_id == poste_sel_id), None)

    if poste_sel:
        # Tableau des missions
        rows_m = []
        for m in sorted(poste_sel.missions, key=lambda x: x['heure']):
            heure = f"{int(m['heure']//60):02d}h{int(m['heure']%60):02d}"
            rows_m.append({
                "Heure"      : heure,
                "Origine"    : m.get('origine',     m.get('site', '')),
                "Destination": m.get('destination', ''),
                "Contenant"  : m['type_contenant'],
                "Qté"        : m['nb_contenants'],
                "Complet"    : '✅' if m.get('est_complet', True) else '⚠️',
                "P/S"        : m.get('propre_sale', ''),
            })
        if rows_m:
            st.dataframe(pd.DataFrame(rows_m),
                         use_container_width=True, hide_index=True)

        # Séquence détaillée
        seq = sequences.get(poste_sel_id, [])
        if seq:
            with st.expander("📋 Séquence détaillée (étapes reconstruites)", expanded=False):
                rows_s = []
                for e in seq:
                    label_e = ETAPES.get(e['type'], (e['type'], ''))[0]
                    rows_s.append({
                        "Étape"  : label_e,
                        "Début"  : f"{int(e['h_debut']//60):02d}h{int(e['h_debut']%60):02d}",
                        "Fin"    : f"{int(e['h_fin']//60):02d}h{int(e['h_fin']%60):02d}",
                        "Durée"  : f"{e['h_fin']-e['h_debut']:.0f} min",
                        "Détail" : e.get('info', ''),
                    })
                st.dataframe(pd.DataFrame(rows_s),
                             use_container_width=True, hide_index=True)

    # ── Charge horaire par type de véhicule ──────────────────────────────
    jobs_par_type = res.get("jobs_par_type", {})
    if jobs_par_type:
        st.divider()
        st.subheader("📈 Charge horaire théorique par type de véhicule")
        import numpy as np
        chart_data = {}
        heures = list(range(24))
        for vt, jlist in jobs_par_type.items():
            vecteur = np.zeros(24)
            for j in jlist:
                h0 = max(0, min(23, int(j.h_dispo    / 60)))
                h1 = max(0, min(23, int(j.h_deadline / 60)))
                if h1 > h0:
                    vecteur[h0:h1] += j.nb_contenants / max(h1 - h0, 1)
            if vecteur.sum() > 0:
                chart_data[vt] = vecteur
        if chart_data:
            labels = [f"{h:02d}h" for h in heures]
            st.bar_chart(pd.DataFrame(chart_data, index=labels))
