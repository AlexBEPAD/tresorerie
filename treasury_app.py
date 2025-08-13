# treasury_app.py
# ---
# Application de gestion de trésorerie (MVP) avec Streamlit + SQLite
# Fonctionnalités :
# - Saisie de transactions (entrées/sorties)
# - Filtre par période, catégorie, recherche texte
# - Table des transactions (édition/suppression)
# - KPIs (solde courant, flux 30j, burn rate, runway)
# - Graphiques : évolution du solde, flux mensuels, répartition par catégorie
# - Import CSV (date, description, catégorie, montant, compte)
# - Export CSV
# - Solde initial configurable (paramètres)
#
# Pour lancer :
#   1) pip install streamlit pandas
#   2) streamlit run treasury_app.py

import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime, date, timedelta
from contextlib import closing

DB_PATH = "treasury.db"

# ---------------------------
# Helpers DB
# ---------------------------

def init_db():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                t_date TEXT NOT NULL,
                description TEXT,
                category TEXT,
                amount REAL NOT NULL,
                account TEXT DEFAULT 'Cash',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        # Valeur par défaut du solde initial
        c.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES('initial_balance', '0')"
        )
        conn.commit()


def get_initial_balance():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        c = conn.cursor()
        c.execute("SELECT value FROM settings WHERE key='initial_balance'")
        row = c.fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0


def set_initial_balance(val: float):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        c = conn.cursor()
        c.execute(
            "REPLACE INTO settings(key, value) VALUES('initial_balance', ?)", (str(val),)
        )
        conn.commit()


def insert_transaction(t_date: date, description: str, category: str, amount: float, account: str):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO transactions(t_date, description, category, amount, account) VALUES (?, ?, ?, ?, ?)",
            (t_date.isoformat() if isinstance(t_date, (date, datetime)) else str(t_date), description, category, amount, account or 'Cash')
        )
        conn.commit()


def update_transaction(tid: int, t_date: date, description: str, category: str, amount: float, account: str):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        c = conn.cursor()
        c.execute(
            "UPDATE transactions SET t_date=?, description=?, category=?, amount=?, account=? WHERE id=?",
            (t_date.isoformat() if isinstance(t_date, (date, datetime)) else str(t_date), description, category, amount, account or 'Cash', tid)
        )
        conn.commit()


def delete_transaction(tid: int):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        c = conn.cursor()
        c.execute("DELETE FROM transactions WHERE id=?", (tid,))
        conn.commit()


def fetch_transactions_df():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        df = pd.read_sql_query("SELECT * FROM transactions ORDER BY t_date ASC, id ASC", conn)
    if not df.empty:
        df['t_date'] = pd.to_datetime(df['t_date']).dt.date
    return df


# ---------------------------
# Analytics helpers
# ---------------------------

def compute_kpis(df: pd.DataFrame, initial_balance: float):
    if df.empty:
        return {
            'current_balance': initial_balance,
            'net_30d': 0.0,
            'avg_daily_burn': 0.0,
            'runway_days': None
        }
    # Solde courant
    current_balance = initial_balance + df['amount'].sum()

    # Net 30 derniers jours
    cutoff = date.today() - timedelta(days=30)
    net_30d = df.loc[df['t_date'] >= cutoff, 'amount'].sum()

    # Burn rate moyen (30 derniers jours si possible, sinon toute l'historique)
    if (df['t_date'] >= cutoff).any():
        period_df = df[df['t_date'] >= cutoff]
        days = (date.today() - max(min(period_df['t_date']), cutoff)).days + 1
    else:
        period_df = df
        days = (max(df['t_date']) - min(df['t_date'])).days + 1
        days = max(days, 30)  # garde-fou
    avg_daily_burn = period_df['amount'].sum() / max(days, 1)

    # Runway : si on brûle (avg_daily_burn < 0)
    runway_days = None
    if avg_daily_burn < 0:
        runway_days = int(current_balance / (-avg_daily_burn)) if current_balance > 0 else 0

    return {
        'current_balance': current_balance,
        'net_30d': net_30d,
        'avg_daily_burn': avg_daily_burn,
        'runway_days': runway_days,
    }


def balance_timeseries(df: pd.DataFrame, initial_balance: float):
    if df.empty:
        return pd.DataFrame({'date': [], 'balance': []})
    tmp = df.copy()
    tmp = tmp.sort_values('t_date')
    tmp['cum'] = tmp['amount'].cumsum() + initial_balance
    ts = tmp.groupby('t_date', as_index=False)['cum'].last()
    ts.rename(columns={'t_date': 'date', 'cum': 'balance'}, inplace=True)
    return ts


def monthly_flows(df: pd.DataFrame):
    if df.empty:
        return pd.DataFrame({'month': [], 'inflow': [], 'outflow': [], 'net': []})
    tmp = df.copy()
    tmp['month'] = pd.to_datetime(tmp['t_date']).dt.to_period('M').dt.to_timestamp()
    inflow = tmp[tmp['amount'] > 0].groupby('month')['amount'].sum()
    outflow = tmp[tmp['amount'] < 0].groupby('month')['amount'].sum()
    net = tmp.groupby('month')['amount'].sum()
    out = pd.DataFrame({'inflow': inflow, 'outflow': outflow, 'net': net}).fillna(0).reset_index()
    return out


def category_breakdown(df: pd.DataFrame):
    if df.empty:
        return pd.DataFrame({'category': [], 'amount': []})
    out = df.groupby('category')['amount'].sum().reset_index()
    out = out.sort_values('amount')
    return out


# ---------------------------
# UI
# ---------------------------

def main():
    st.set_page_config(page_title="Trésorerie", page_icon="💶", layout="wide")
    st.title("💶 Gestion de trésorerie — MVP")
    init_db()

    # Sidebar - paramètres
    with st.sidebar:
        st.header("Paramètres")
        initial_balance = get_initial_balance()
        new_initial = st.number_input("Solde initial (€)", value=float(initial_balance), step=100.0, format="%0.2f")
        if st.button("Enregistrer le solde initial"):
            set_initial_balance(new_initial)
            st.success("Solde initial mis à jour.")

        st.markdown("---")
        st.subheader("Import CSV")
        st.caption("Colonnes attendues : date (YYYY-MM-DD), description, category, amount, account")
        up = st.file_uploader("Importer des transactions", type=["csv"]) 
        if up is not None:
            try:
                imp = pd.read_csv(up)
                required = {"date", "description", "category", "amount"}
                if not required.issubset(set(map(str.lower, imp.columns))):
                    st.error("Colonnes manquantes. Requis : date, description, category, amount (et optionnel: account)")
                else:
                    # Normaliser
                    cols = {c.lower(): c for c in imp.columns}
                    for col in ["date", "description", "category", "amount", "account"]:
                        if col not in cols:
                            imp[col] = None
                    imp['date'] = pd.to_datetime(imp['date']).dt.date
                    for _, r in imp.iterrows():
                        insert_transaction(r['date'], r['description'] or '', r['category'] or 'Autre', float(r['amount']), r.get('account') or 'Cash')
                    st.success(f"Import terminé : {len(imp)} lignes.")
            except Exception as e:
                st.error(f"Échec import: {e}")

        if st.button("Exporter CSV"):
            df_all = fetch_transactions_df()
            if df_all.empty:
                st.warning("Aucune transaction à exporter.")
            else:
                csv = df_all.to_csv(index=False).encode('utf-8')
                st.download_button("Télécharger transactions.csv", csv, file_name="transactions.csv", mime="text/csv")

    # Onglets
    tab_dash, tab_tx, tab_edit = st.tabs(["📊 Dashboard", "📜 Transactions", "➕ Ajouter / ✏️ Éditer"])

    # Chargement données + filtres communs
    df = fetch_transactions_df()

    with tab_dash:
        st.subheader("Indicateurs clés")
        kpi = compute_kpis(df, get_initial_balance())
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Solde courant", f"{kpi['current_balance']:.2f} €")
        col2.metric("Net 30 jours", f"{kpi['net_30d']:.2f} €")
        col3.metric("Burn moyen/jour", f"{kpi['avg_daily_burn']:.2f} €")
        runway = f"{kpi['runway_days']} j" if kpi['runway_days'] is not None else "—"
        col4.metric("Runway estimé", runway)

        st.markdown("---")
        st.subheader("Évolution du solde")
        ts = balance_timeseries(df, get_initial_balance())
        if ts.empty:
            st.info("Ajoutez des transactions pour voir le graphique.")
        else:
            st.line_chart(ts.set_index('date')['balance'])

        st.markdown("---")
        st.subheader("Flux mensuels")
        mf = monthly_flows(df)
        if mf.empty:
            st.info("Pas encore de flux.")
        else:
            st.bar_chart(mf.set_index('month')[['inflow','outflow','net']])

        st.markdown("---")
        st.subheader("Répartition par catégorie (net)")
        cb = category_breakdown(df)
        if cb.empty:
            st.info("Pas encore de catégories.")
        else:
            st.bar_chart(cb.set_index('category')['amount'])

    with tab_tx:
        st.subheader("Filtrer")
        colf1, colf2, colf3 = st.columns([1,1,2])
        min_d = df['t_date'].min() if not df.empty else date.today()
        max_d = df['t_date'].max() if not df.empty else date.today()
        d1, d2 = colf1.date_input("Période", value=(min_d, max_d)) if not df.empty else (date.today(), date.today())
        cats = sorted([c for c in df['category'].dropna().unique()]) if not df.empty else []
        cat_sel = colf2.multiselect("Catégories", cats)
        q = colf3.text_input("Recherche (description)")

        dfv = df.copy()
        if not dfv.empty:
            if isinstance(d1, date) and isinstance(d2, date):
                dfv = dfv[(dfv['t_date'] >= d1) & (dfv['t_date'] <= d2)]
            if cat_sel:
                dfv = dfv[dfv['category'].isin(cat_sel)]
            if q:
                dfv = dfv[dfv['description'].str.contains(q, case=False, na=False)]

        st.dataframe(dfv.sort_values(['t_date','id'], ascending=[False, False]), use_container_width=True, height=420)

        st.markdown("Supprimer une ligne")
        del_id = st.number_input("ID à supprimer", min_value=0, step=1)
        if st.button("Supprimer"):
            if del_id > 0:
                delete_transaction(int(del_id))
                st.success("Transaction supprimée.")
            else:
                st.warning("Indiquez un ID valide.")

    with tab_edit:
        st.subheader("Ajouter une transaction")
        with st.form("add_form"):
            c1, c2 = st.columns(2)
            t_date = c1.date_input("Date", value=date.today())
            amount = c2.number_input("Montant (€) — positif = entrée, négatif = sortie", value=0.0, step=50.0, format="%0.2f")
            description = st.text_input("Description")
            category = st.text_input("Catégorie", value="Autre")
            account = st.text_input("Compte", value="Cash")
            submitted = st.form_submit_button("Ajouter")
            if submitted:
                try:
                    insert_transaction(t_date, description, category, float(amount), account)
                    st.success("Ajouté !")
                except Exception as e:
                    st.error(f"Échec ajout: {e}")

        st.markdown("---")
        st.subheader("Éditer une transaction existante")
        if df.empty:
            st.info("Aucune transaction à éditer.")
        else:
            ids = df['id'].tolist()
            sel = st.selectbox("Choisir l'ID", ids)
            row = df[df['id'] == sel].iloc[0]
            with st.form("edit_form"):
                c1, c2 = st.columns(2)
                e_date = c1.date_input("Date", value=row['t_date'])
                e_amount = c2.number_input("Montant (€)", value=float(row['amount']), step=50.0, format="%0.2f")
                e_desc = st.text_input("Description", value=row['description'] or "")
                e_cat = st.text_input("Catégorie", value=row['category'] or "Autre")
                e_acc = st.text_input("Compte", value=row['account'] or "Cash")
                save = st.form_submit_button("Enregistrer")
                if save:
                    try:
                        update_transaction(int(sel), e_date, e_desc, e_cat, float(e_amount), e_acc)
                        st.success("Modifications enregistrées.")
                    except Exception as e:
                        st.error(f"Échec mise à jour: {e}")


if __name__ == "__main__":
    main()
