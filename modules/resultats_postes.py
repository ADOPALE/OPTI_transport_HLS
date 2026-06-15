"""
resultats_postes.py — Affichage Streamlit des résultats du moteur transport
===========================================================================

Fonctions d'affichage pour l'onglet « Synthèse transport » :
  - afficher_recap_jours(resultats)       : tableau hebdo flotte/postes
  - afficher_jour(res)                     : détail d'un jour (KPI, Gantt, tables)
  - afficher_non_servis(resultats)         : flux non servis + contrainte bloquante
"""

import pandas as pd
import streamlit as st

try:
    import plotly.express as px
    import plotly.graph_objects as go
    _PLOTLY = True
except Exception:
    _PLOTLY = False

# couleurs par type d'étape
_COULEURS = {
    "MISSION": "#2E86C1", "APPROCHE_VIDE": "#E67E22", "RETOUR_VIDE": "#D35400",
    "MARGE": "#95A5A6", "NETTOYAGE": "#16A085", "PAUSE": "#C0392B",
    "ATTENTE": "#AEB6BF", "DISPONIBLE": "#D5DBDB", "PRISE": "#7D3C98", "FIN": "#5D4037",
}
_LIBELLE = {
    "MISSION": "Mission (chargé)", "APPROCHE_VIDE": "Approche à vide",
    "RETOUR_VIDE": "Retour à vide", "MARGE": "Marge inter-job",
    "NETTOYAGE": "Désinfection", "PAUSE": "Pause", "ATTENTE": "Attente",
    "DISPONIBLE": "Disponible", "PRISE": "Prise de poste", "FIN": "Clôture",
}


def _hhmm(m):
    try:
        return f"{int(m // 60):02d}:{int(round(m % 60)):02d}"
    except Exception:
        return ""


def afficher_recap_jours(resultats):
    """Tableau de synthèse hebdomadaire + alerte de validité."""
    total_ns = sum(len(r.get("non_servis", [])) for r in resultats.values())
    total_anomalies = sum(r.get("audit", {}).get("nb_anomalies", 0)
                          for r in resultats.values())
    if total_ns == 0 and total_anomalies == 0:
        st.success("✅ Solution **valide** : tous les flux du périmètre sont planifiés.")
    else:
        st.error(f"❌ Solution **non valide** : {total_ns} flux non servi(s), "
                 f"{total_anomalies} anomalie(s) technique(s).")

    lignes = []
    for jour, r in resultats.items():
        m = r["metriques"]
        lignes.append({
            "Jour": jour, "Flux servis": m["nb_missions"], "Postes": m["nb_postes"],
            "Véhicules": m["nb_vehicules_total"], "Pic simultané": m["pic_vehicules_simultanes"],
            "Non servis": m["nb_flux_non_servis"],
            "Postes <80 %": m.get("nb_postes_sous_80", 0),
            "Km à vide %": m["taux_km_vide"], "Occupation %": m["occupation_moyenne"],
        })
    df = pd.DataFrame(lignes)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # flotte par type (max hebdo)
    types = sorted({t for r in resultats.values() for t in r["nb_vehicules"].keys()})
    if types:
        data = {"Type": types}
        for jour, r in resultats.items():
            data[jour] = [r["nb_vehicules"].get(t, 0) for t in types]
        data["Maxi semaine"] = [max(r["nb_vehicules"].get(t, 0) for r in resultats.values())
                                for t in types]
        st.markdown("**Flotte par type de véhicule** (à dimensionner = maxi hebdomadaire)")
        st.dataframe(pd.DataFrame(data), use_container_width=True, hide_index=True)


def afficher_jour(res):
    """Détail d'un jour : KPI, Gantt des postes, courbe de concurrence, quais."""
    m = res["metriques"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Véhicules", m["nb_vehicules_total"])
    c2.metric("Postes", m["nb_postes"])
    c3.metric("Km à vide", f"{m['taux_km_vide']} %")
    c4.metric("Occupation moy.", f"{m['occupation_moyenne']} %")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Flux servis", m["nb_missions"])
    c2.metric("Flux non servis", m["nb_flux_non_servis"])
    c3.metric("Postes <80 %", m.get("nb_postes_sous_80", 0))
    c4.metric("Calcul", f"{m['temps_calcul_s']} s")

    audit = res.get("audit", {})
    if audit.get("nb_anomalies", 0):
        with st.expander("Anomalies techniques bloquantes", expanded=True):
            for anomalie in audit.get("anomalies", []):
                st.error(anomalie)

    if _PLOTLY:
        _gantt(res)
        _courbe_concurrence(res)
    else:
        st.info("Installez plotly pour les graphiques (Gantt, concurrence).")

    # quais
    quais = res.get("quais", {})
    if quais:
        with st.expander("🏗️ Pic de véhicules à quai par site"):
            dfq = pd.DataFrame(sorted(quais.items(), key=lambda x: -x[1]),
                               columns=["Site", "Pic simultané"])
            st.dataframe(dfq, use_container_width=True, hide_index=True)


def _gantt(res):
    lignes = []
    for p in sorted(res["postes"], key=lambda p: (p.v_type, p.id_vehicule, p.h_debut)):
        for e in p.etapes:
            if e.duree <= 0:
                continue
            lignes.append({
                "Véhicule": f"{p.id_vehicule}", "Poste": p.id,
                "Début": e.h_debut, "Fin": e.h_fin, "Durée": e.duree,
                "Étape": _LIBELLE.get(e.type, e.type), "type": e.type,
                "Détail": e.detail,
            })
    if not lignes:
        return
    df = pd.DataFrame(lignes)
    fig = go.Figure()
    vehicules = list(dict.fromkeys(df["Véhicule"]))
    ypos = {v: i for i, v in enumerate(vehicules)}
    for _, r in df.iterrows():
        fig.add_trace(go.Bar(
            x=[r["Durée"]], y=[ypos[r["Véhicule"]]], base=[r["Début"]], orientation="h",
            marker=dict(color=_COULEURS.get(r["type"], "#888")), showlegend=False,
            hovertemplate=f"{r['Véhicule']} — {r['Étape']}<br>"
                          f"{_hhmm(r['Début'])}→{_hhmm(r['Fin'])}<br>{r['Détail']}<extra></extra>",
        ))
    fig.update_layout(
        title="Planning des postes (un véhicule par ligne ; relève = 2 postes)",
        barmode="stack", height=max(300, 28 * len(vehicules) + 120),
        xaxis=dict(title="Heure", tickmode="array",
                   tickvals=list(range(360, 1320, 60)),
                   ticktext=[_hhmm(t) for t in range(360, 1320, 60)], range=[330, 1290]),
        yaxis=dict(tickmode="array", tickvals=list(ypos.values()),
                   ticktext=list(ypos.keys()), autorange="reversed"),
        margin=dict(l=10, r=10, t=40, b=10),
    )
    st.plotly_chart(fig, use_container_width=True)
    # légende
    leg = " &nbsp; ".join(f"<span style='color:{_COULEURS[k]}'>■</span> {_LIBELLE[k]}"
                          for k in ["MISSION", "APPROCHE_VIDE", "RETOUR_VIDE", "MARGE",
                                    "NETTOYAGE", "PAUSE", "ATTENTE"])
    st.markdown(leg, unsafe_allow_html=True)


def _courbe_concurrence(res):
    conc = res.get("concurrence", {})
    bins, vals = conc.get("bins", []), conc.get("valeurs", [])
    if not bins:
        return
    fig = go.Figure(go.Scatter(x=[_hhmm(b) for b in bins], y=vals, fill="tozeroy",
                               mode="lines", line=dict(color="#2E86C1")))
    fig.update_layout(title="Postes simultanés au fil de la journée (= besoin instantané en véhicules)",
                      height=240, margin=dict(l=10, r=10, t=40, b=10),
                      xaxis_title="Heure", yaxis_title="Postes simultanés")
    st.plotly_chart(fig, use_container_width=True)


def afficher_non_servis(resultats):
    """Liste détaillée des flux non servis avec la contrainte bloquante."""
    lignes = []
    for jour, r in resultats.items():
        for ns in r.get("non_servis", []):
            lignes.append({
                "Jour": jour, "Flux": ns.get("flux_id"), "Origine": ns.get("origine"),
                "Destination": ns.get("destination"), "Contenant": ns.get("contenant"),
                "Pourquoi": ns.get("raison"), "Contrainte bloquante": ns.get("contrainte"),
            })
    if not lignes:
        st.success("✅ Aucun flux non servi.")
        return
    st.error(f"❌ {len(lignes)} flux non servi(s) — à corriger pour une solution valide :")
    st.dataframe(pd.DataFrame(lignes), use_container_width=True, hide_index=True)
