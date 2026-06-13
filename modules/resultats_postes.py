"""
resultats_postes.py — Affichage des résultats du moteur de chaînage (V2)
=========================================================================

Rend les objets `Poste` / `Etape` produits par `moteur_postes.optimiser_postes_jour` :
  - récapitulatif hebdomadaire (avec pic de véhicules simultanés + fenêtres tendues)
  - courbe de concurrence (preuve du lissage : pas de pic matinal)
  - Gantt des postes groupés par véhicule (montre la relève matin / après-midi)
  - détail tabulaire par poste
  - détail des passages par site
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


COULEURS = {
    "MISSION":       ("Chargé",         "#1f77b4"),
    "APPROCHE_VIDE": ("Trajet à vide",  "#ff7f0e"),
    "RETOUR_VIDE":   ("Retour à vide",  "#ff7f0e"),
    "ATTENTE":       ("Attente",        "#aec7e8"),
    "DISPONIBLE":    ("Dispo au dépôt", "#d9d9d9"),
    "PAUSE":         ("Pause (dépôt)",  "#d62728"),
    "NETTOYAGE":     ("Nettoyage",      "#17becf"),
    "PRISE":         ("Prise de poste", "#9467bd"),
    "FIN":           ("Clôture",        "#8c564b"),
}


def _fmt(m):
    try:
        return f"{int(m // 60):02d}h{int(m % 60):02d}"
    except Exception:
        return "--:--"


# ---------------------------------------------------------------------
# 1. Récapitulatif hebdomadaire
# ---------------------------------------------------------------------

def afficher_recap_jours(resultats_par_jour):
    lignes = []
    for jour, res in resultats_par_jour.items():
        m = res["metriques"]
        ligne = {
            "Jour": jour,
            "Missions": m["nb_missions"],
            "Postes": m["nb_postes"],
            "🚚 Véhicules": m["nb_vehicules_total"],
            "Pic simultané": m["pic_vehicules_simultanes"],
            "Chargé/roulage": f"{m['taux_charge_global']}%",
            "À vide (min)": int(m["temps_vide_min"]),
            "⚠️ Fenêtres tendues": m["nb_missions_non_traitees"],
        }
        for vt, n in m["nb_vehicules_par_type"].items():
            ligne[f"Véh. {vt}"] = n
        lignes.append(ligne)
    df = pd.DataFrame(lignes).fillna(0)
    st.dataframe(df, use_container_width=True, hide_index=True)

    total_tendues = sum(r["metriques"]["nb_missions_non_traitees"] for r in resultats_par_jour.values())
    if total_tendues:
        st.caption("⚠️ Les « fenêtres tendues » sont des flux dont la fenêtre horaire du fichier "
                   "est incohérente (livraison avant mise à disposition) ou trop courte pour la durée "
                   "de la mission. Ils sont planifiés malgré tout, mais à vérifier dans l'Excel source.")


# ---------------------------------------------------------------------
# 2. Courbe de concurrence (preuve du lissage)
# ---------------------------------------------------------------------

def afficher_courbe_concurrence(res):
    conc = res.get("concurrence", {})
    bins, valeurs = conc.get("bins", []), conc.get("valeurs", [])
    if not bins:
        return
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=bins, y=valeurs, mode="lines", fill="tozeroy",
        line=dict(color="#00558E", width=2), name="Véhicules actifs",
        hovertemplate="%{customdata}<br>%{y} véhicule(s)<extra></extra>",
        customdata=[_fmt(b) for b in bins],
    ))
    pic = max(valeurs) if valeurs else 0
    fig.add_hline(y=pic, line_dash="dash", line_color="#d62728",
                  annotation_text=f"Pic = {pic}", annotation_position="top left")
    fig.update_layout(
        title="Véhicules simultanés au cours de la journée (lissage)",
        height=280,
        xaxis=dict(title="Heure", tickvals=list(range(360, 1321, 120)),
                   ticktext=[f"{h // 60}h" for h in range(360, 1321, 120)]),
        yaxis=dict(title="Véhicules actifs", rangemode="tozero"),
        margin=dict(l=10, r=10, t=50, b=30), showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Une courbe plate = charge bien lissée sur la journée (pas de pic matinal). "
               "Le nombre de véhicules dimensionné correspond à ce pic, par type.")


# ---------------------------------------------------------------------
# 3. Gantt des postes (groupés par véhicule -> montre la relève)
# ---------------------------------------------------------------------

def afficher_gantt_postes(postes, titre="Planning", grouper_par_vehicule=True):
    if not postes:
        st.info("Aucun poste à afficher.")
        return

    if grouper_par_vehicule:
        cle = lambda p: (p.v_type, p.id_vehicule, p.shift, p.h_debut)
        etiquette = lambda p: p.id_vehicule or p.id
    else:
        cle = lambda p: (p.v_type, p.id, p.h_debut)
        etiquette = lambda p: p.id

    postes = sorted(postes, key=cle)
    fig = go.Figure()
    deja = set()
    for p in postes:
        y = etiquette(p)
        for e in p.etapes:
            duree = e.h_fin - e.h_debut
            if duree <= 0:
                continue
            label, couleur = COULEURS.get(e.type, (e.type, "#7f7f7f"))
            montrer = label not in deja
            deja.add(label)
            fig.add_trace(go.Bar(
                base=[e.h_debut], x=[duree], y=[y], orientation="h",
                marker=dict(color=couleur, line=dict(width=0)),
                name=label, legendgroup=label, showlegend=montrer,
                hovertemplate=(f"<b>{p.id}</b> ({'matin' if p.shift == 0 else 'après-midi'})"
                               f" — {label}<br>{_fmt(e.h_debut)} → {_fmt(e.h_fin)} "
                               f"({duree:.0f} min)<br>{e.detail}<extra></extra>"),
            ))

    n = len({etiquette(p) for p in postes})
    fig.update_layout(
        title=titre, barmode="stack", height=260 + n * 30,
        xaxis=dict(title="Heure", range=[340, 1280],
                   tickvals=list(range(360, 1261, 60)),
                   ticktext=[f"{h // 60}h" for h in range(360, 1261, 60)],
                   gridcolor="lightgray"),
        yaxis=dict(autorange="reversed", title="Véhicule"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(l=10, r=10, t=70, b=40),
        hoverlabel=dict(bgcolor="black", font_size=12),
    )
    st.plotly_chart(fig, use_container_width=True)
    if grouper_par_vehicule:
        st.caption("Chaque ligne = un véhicule physique. Deux postes sur la même ligne "
                   "(matin + après-midi) = relève de chauffeurs.")


# ---------------------------------------------------------------------
# 4. Détail par poste
# ---------------------------------------------------------------------

def afficher_detail_postes(postes):
    for p in sorted(postes, key=lambda p: (p.id_vehicule, p.shift)):
        creneau = "matin" if p.shift == 0 else ("après-midi" if p.shift == 1 else f"créneau {p.shift + 1}")
        with st.expander(
            f"🚛 {p.id_vehicule or p.id}  ·  {creneau}  |  {_fmt(p.h_debut)} → {_fmt(p.h_fin)}  "
            f"|  {len(p.missions)} mission(s)  |  chargé/roulage {p.taux_charge_roulage() * 100:.0f}%",
            expanded=False,
        ):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("⏱ Chargé", f"{p.temps_charge():.0f} min")
            c2.metric("⬜ À vide", f"{p.temps_vide():.0f} min")
            c3.metric("⏳ Inactif", f"{p.temps_attente():.0f} min")
            c4.metric("📐 Durée poste", f"{p.amplitude:.0f} min")

            lignes = []
            for e in p.etapes:
                label = COULEURS.get(e.type, (e.type, ""))[0]
                lignes.append({
                    "Début": _fmt(e.h_debut), "Fin": _fmt(e.h_fin),
                    "Durée": f"{e.h_fin - e.h_debut:.0f} min",
                    "Type": label, "Détail": e.detail,
                })
            st.dataframe(pd.DataFrame(lignes), use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------
# 5. Détail par site
# ---------------------------------------------------------------------

def afficher_detail_sites(postes):
    passages = []
    for p in postes:
        for e in p.etapes:
            if e.type != "MISSION" or e.mission is None:
                continue
            ancre = e.h_debut
            arrivee = None
            for et in e.mission.etapes:
                if et["action"] == "MISE_A_QUAI":
                    arrivee = ancre + et["t_debut"]
                elif et["action"] in ("CHARGEMENT", "DECHARGEMENT"):
                    sens = "PRISE" if et["action"] == "CHARGEMENT" else "DÉPOSE"
                    passages.append({
                        "site": et["site"], "Véhicule": p.id_vehicule or p.id,
                        "Arrivée quai": _fmt(arrivee) if arrivee else "—",
                        "Début": _fmt(ancre + et["t_debut"]),
                        "Fin": _fmt(ancre + et["t_fin"]),
                        "Opération": sens, "Détail": et["label"],
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
