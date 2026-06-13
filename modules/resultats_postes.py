"""
resultats_postes.py — Affichage des résultats du moteur de chaînage
====================================================================

Rend les objets `Poste` / `Etape` produits par `moteur_postes.optimiser_postes_jour` :
  - récapitulatif hebdomadaire
  - Gantt des postes (étapes chargées / à vide / attente / pause)
  - détail tabulaire par poste
  - détail des passages par site

Conçu pour remplacer l'affichage de `Resultats_simul_flux` dans l'onglet
« Synthèse transport ».
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


# Couleurs par type d'étape (cohérentes avec l'ancien Gantt)
COULEURS = {
    "MISSION":       ("Chargé",        "#1f77b4"),
    "APPROCHE_VIDE": ("Trajet à vide", "#ff7f0e"),
    "RETOUR_VIDE":   ("Retour à vide", "#ff7f0e"),
    "ATTENTE":       ("Attente",       "#aec7e8"),
    "PAUSE":         ("Pause",         "#d62728"),
    "NETTOYAGE":     ("Nettoyage",     "#17becf"),
    "PRISE":         ("Prise de poste","#9467bd"),
    "FIN":           ("Fin de poste",  "#8c564b"),
}


def _fmt(m):
    """Minutes depuis minuit -> 'HHhMM'."""
    try:
        return f"{int(m // 60):02d}h{int(m % 60):02d}"
    except Exception:
        return "--:--"


# ---------------------------------------------------------------------
# 1. Récapitulatif hebdomadaire
# ---------------------------------------------------------------------

def afficher_recap_jours(resultats_par_jour):
    """resultats_par_jour : {jour: dict renvoyé par optimiser_postes_jour}."""
    lignes, types_vus = [], set()
    for jour, res in resultats_par_jour.items():
        m = res["metriques"]
        types_vus.update(m["nb_vehicules_par_type"].keys())
        ligne = {
            "Jour": jour,
            "Missions": m["nb_missions"],
            "Postes": m["nb_postes"],
            "Véhicules": m["nb_vehicules_total"],
            "Chargé/roulage": f"{m['taux_charge_global']}%",
            "À vide (min)": int(m["temps_vide_min"]),
            "Attente (min)": int(m["temps_attente_min"]),
        }
        for vt in m["nb_vehicules_par_type"]:
            ligne[f"Véh. {vt}"] = m["nb_vehicules_par_type"][vt]
        lignes.append(ligne)
    df = pd.DataFrame(lignes).fillna(0)
    st.dataframe(df, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------
# 2. Gantt des postes
# ---------------------------------------------------------------------

def afficher_gantt_postes(postes, titre="Planning"):
    if not postes:
        st.info("Aucun poste à afficher.")
        return

    fig = go.Figure()
    deja_legende = set()

    # tri pour un affichage stable (par véhicule puis heure de début)
    postes = sorted(postes, key=lambda p: (p.id_vehicule or p.id, p.h_debut))

    for p in postes:
        for e in p.etapes:
            duree = e.h_fin - e.h_debut
            if duree <= 0:
                continue
            label, couleur = COULEURS.get(e.type, (e.type, "#7f7f7f"))
            montrer = label not in deja_legende
            deja_legende.add(label)
            fig.add_trace(go.Bar(
                base=[e.h_debut],
                x=[duree],
                y=[p.id],
                orientation="h",
                marker_color=couleur,
                name=label,
                legendgroup=label,
                showlegend=montrer,
                hovertemplate=(
                    f"<b>{p.id}</b> — {label}<br>"
                    f"{_fmt(e.h_debut)} → {_fmt(e.h_fin)} ({duree:.0f} min)<br>"
                    f"{e.detail}<extra></extra>"
                ),
            ))

    fig.update_layout(
        title=titre,
        barmode="stack",
        height=300 + len(postes) * 28,
        xaxis=dict(
            title="Heure",
            range=[300, 1320],
            tickvals=list(range(300, 1321, 60)),
            ticktext=[f"{h // 60}h" for h in range(300, 1321, 60)],
            gridcolor="lightgray",
        ),
        yaxis=dict(autorange="reversed", title="Poste"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(l=10, r=10, t=60, b=40),
        hoverlabel=dict(bgcolor="black", font_size=12),
    )
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------
# 3. Détail par poste
# ---------------------------------------------------------------------

def afficher_detail_postes(postes):
    for p in postes:
        veh = f"  —  {p.id_vehicule}" if p.id_vehicule else ""
        with st.expander(
            f"🚛 {p.id}{veh}  |  {_fmt(p.h_debut)} → {_fmt(p.h_fin)}  "
            f"|  {len(p.missions)} mission(s)  |  chargé/roulage {p.taux_charge_roulage() * 100:.0f}%",
            expanded=False,
        ):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("⏱ Chargé", f"{p.temps_charge():.0f} min")
            c2.metric("⬜ À vide", f"{p.temps_vide():.0f} min")
            c3.metric("⏳ Attente", f"{p.temps_attente():.0f} min")
            c4.metric("📐 Amplitude", f"{p.amplitude:.0f} min")

            lignes = []
            for e in p.etapes:
                label = COULEURS.get(e.type, (e.type, ""))[0]
                lignes.append({
                    "Début": _fmt(e.h_debut),
                    "Fin": _fmt(e.h_fin),
                    "Durée": f"{e.h_fin - e.h_debut:.0f} min",
                    "Type": label,
                    "Détail": e.detail,
                })
            st.dataframe(pd.DataFrame(lignes), use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------
# 4. Détail par site (passages reconstruits depuis les chronologies)
# ---------------------------------------------------------------------

def afficher_detail_sites(postes):
    passages = []
    for p in postes:
        for e in p.etapes:
            if e.type != "MISSION" or e.mission is None:
                continue
            ancre = e.h_debut
            arrivee_quai = None
            for et in e.mission.etapes:
                if et["action"] == "MISE_A_QUAI":
                    arrivee_quai = ancre + et["t_debut"]
                elif et["action"] in ("CHARGEMENT", "DECHARGEMENT"):
                    sens = "PRISE" if et["action"] == "CHARGEMENT" else "DÉPOSE"
                    passages.append({
                        "site": et["site"],
                        "Poste": p.id,
                        "Arrivée quai": _fmt(arrivee_quai) if arrivee_quai else "—",
                        "Début": _fmt(ancre + et["t_debut"]),
                        "Fin": _fmt(ancre + et["t_fin"]),
                        "Opération": sens,
                        "Détail": et["label"],
                        "_sort": ancre + et["t_debut"],
                    })

    if not passages:
        st.info("Aucun passage à afficher.")
        return

    sites = sorted({pa["site"] for pa in passages})
    site_sel = st.selectbox("Sélectionner un site", sites, key="site_postes")
    sous = sorted([pa for pa in passages if pa["site"] == site_sel], key=lambda x: x["_sort"])
    df = pd.DataFrame(sous).drop(columns=["site", "_sort"])
    st.dataframe(df, use_container_width=True, hide_index=True)
