from flask import Flask, render_template
from app.extensions import db, bcrypt, login_manager
from app.auth.routes import auth


def create_app():
    app = Flask(__name__)

    app.config.from_object("config.Config")

    db.init_app(app)
    app.register_blueprint(auth)
    bcrypt.init_app(app)
    login_manager.init_app(app)

    @app.route("/")
    def home():
        return render_template("shared/index.html")
    with app.app_context():
        db.create_all()
    return app
